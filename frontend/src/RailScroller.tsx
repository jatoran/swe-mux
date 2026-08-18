import type { ComponentChildren, JSX } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { holdSoftKeyboard, restoreSoftKeyboard, softKeyboardDismissals, softKeyboardHolder } from './mobileKeyboard'
import { horizontalWheelDelta } from './wheelScroll'
import { pointerDragOwnsPointer } from './pointerDragClaim'
import {
  railFocusTarget,
  railOverflowState,
  railPageTarget,
  RAIL_PAN_SLOP_PX,
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
  focusKey?: string
  stripProps?: OverflowRailStripProps
  touchDrag?: boolean
  touchDragGain?: number
  preserveSoftKeyboard?: boolean
}

type TouchDragState = {
  pointerId: number
  startX: number
  startY: number
  startScrollLeft: number
  dragging: boolean
  cancelled: boolean
  keyboardHolder: HTMLElement | null
  keyboardDismissals: number
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
  focusKey,
  stripProps,
  touchDrag = false,
  touchDragGain = 1,
  preserveSoftKeyboard = false,
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

  const scheduleSelectedReveal = () => {
    const frame = requestAnimationFrame(() => {
      const strip = stripRef.current
      const selected = strip?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
      if (strip && selected) revealItem(strip, selected)
      syncOverflow()
    })
    return () => cancelAnimationFrame(frame)
  }

  useEffect(() => {
    if (activeKey === undefined) return
    return scheduleSelectedReveal()
  }, [activeKey])

  // A pane can receive workspace focus while its already-selected tab stays the
  // same. That focus transition must reveal the tab too, without making a rail
  // that just lost focus snap back to its selection.
  useEffect(() => {
    if (focusKey === undefined) return
    return scheduleSelectedReveal()
  }, [focusKey])

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
    if (preserveSoftKeyboard && state.keyboardHolder) {
      requestAnimationFrame(() => restoreSoftKeyboard(state.keyboardHolder, state.keyboardDismissals))
    }
  }

  return <div
    class={`overflow-rail ${touchDrag ? 'overflow-rail-touch-drag' : ''} ${wrapperClassName}`.trim()}
    onMouseDown={preserveSoftKeyboard ? holdSoftKeyboard : undefined}
    onPointerDown={event => {
      if (!touchDrag || event.pointerType !== 'touch' || !event.isPrimary) return
      // Every button on the rail is a legal place to begin a swipe, the repeating arrow
      // keys included. They used to be excluded here so their hold-to-repeat kept
      // priority, which made the arrows the one part of the rail you could not push off
      // of: the flick meant to scroll the strip fired the key instead. The arrows now
      // send on `click` like their neighbours, so the suppression below settles it.
      const strip = stripRef.current
      if (!strip || strip.scrollWidth <= strip.clientWidth + 1) return
      touchDragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startScrollLeft: strip.scrollLeft,
        dragging: false,
        cancelled: false,
        keyboardHolder: preserveSoftKeyboard ? softKeyboardHolder() : null,
        keyboardDismissals: softKeyboardDismissals(),
      }
      event.currentTarget.setPointerCapture(event.pointerId)
    }}
    onPointerMove={event => {
      const state = touchDragRef.current
      const strip = stripRef.current
      if (!state || !strip || state.pointerId !== event.pointerId || state.cancelled) return
      // A hold-then-drag reorder (drawer tabs on mobile) shares this pointer and this same
      // horizontal motion. The instant it claims the pointer, this pan stands down and hands
      // the gesture over, rather than scrolling the strip out from under the drag.
      if (pointerDragOwnsPointer()) { state.cancelled = true; return }
      const dx = event.clientX - state.startX
      const dy = event.clientY - state.startY
      if (!state.dragging) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < RAIL_PAN_SLOP_PX) return
        if (Math.abs(dy) >= Math.abs(dx)) { state.cancelled = true; return }
        state.dragging = true
      }
      event.preventDefault()
      strip.scrollLeft = state.startScrollLeft - dx * touchDragGain
      syncOverflow()
    }}
    onPointerUp={event => finishTouchDrag(event.currentTarget, event.pointerId, false)}
    onPointerCancel={event => finishTouchDrag(event.currentTarget, event.pointerId, true)}
    onLostPointerCapture={event => finishTouchDrag(event.currentTarget, event.pointerId, true)}
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
  return <OverflowRail className="terminal-action-scroll" itemLabel="commands" wrapperClassName="terminal-action-scroller" touchDrag touchDragGain={1.75} preserveSoftKeyboard>
    {children}
  </OverflowRail>
}
