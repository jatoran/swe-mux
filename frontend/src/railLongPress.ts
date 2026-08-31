import { useEffect, useRef } from 'preact/hooks'
import type { JSX } from 'preact'

/**
 * The three ways into a context menu on a chip that lives inside an `OverflowRail`.
 *
 * This exists because one of them is a trap that is invisible in review. `OverflowRail`
 * captures the pointer for its own horizontal pan the moment a touch lands, which retargets
 * every later pointer event to the rail - so an element-local `pointermove`/`pointerup` pair
 * never fires, the cancel never runs, and the hold timer opens a menu partway through
 * somebody's scroll. The pending press has to be watched on `window`.
 *
 * `NotesTab.tsx` learned that first and still implements it inline; its renderer spec
 * (`notes-tab-actions.spec.ts`) is the regression test for the trap. This module is the same
 * rule extracted so the next rail does not have to rediscover it. Converting Notes onto it is
 * safe but is a separate change - it has its own touch semantics for empty rail space.
 */

const LONG_PRESS_MS = 550
/** How long after a touch-opened menu a click is treated as the gesture's own echo. */
const TOUCH_ECHO_MS = 250
/** Movement past this cancels the hold and lets the rail keep the pan. */
const SLOP_PX = 10

/** Where the menu should appear. Viewport coordinates, as every menu in the app takes. */
export type MenuOpener = (x: number, y: number) => void

export type RailLongPress = {
  /** `onPointerDown` on the chip. Only a touch arms the hold; a mouse has right-click. */
  begin: (event: JSX.TargetedPointerEvent<HTMLElement>, open: MenuOpener) => void
  /** `onContextMenu` on the chip, or on empty rail space. */
  contextMenu: (event: JSX.TargetedMouseEvent<HTMLElement>, open: MenuOpener) => void
  /** `onKeyDown` on the chip. Returns true when it handled the key, so the caller can stop.
   *  A keyboard has neither of the pointer gestures and a rail chip has no inline actions to
   *  fall back on, so without this the menu is simply unreachable without a pointer. */
  keyboardMenu: (event: JSX.TargetedKeyboardEvent<HTMLElement>, open: MenuOpener) => boolean
  /** `onClickCapture` on the chip. Swallows the click a completed hold already answered. */
  suppressClick: (event: JSX.TargetedMouseEvent<HTMLElement>) => void
  /** `onClickCapture` on the opened menu. A touch that opens a menu under the finger also
   *  delivers a click there; without this the first item fires itself. */
  suppressMenuEcho: (event: JSX.TargetedMouseEvent<HTMLElement>) => void
}

export function useRailLongPress(holdMs = LONG_PRESS_MS): RailLongPress {
  const timer = useRef<number | null>(null)
  const watch = useRef<(() => void) | null>(null)
  const touched = useRef(false)
  const suppress = useRef(false)
  const openedAt = useRef(0)

  const cancel = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
    watch.current?.()
    watch.current = null
  }

  useEffect(() => cancel, [])

  const stamp = (open: MenuOpener, x: number, y: number) => {
    openedAt.current = performance.now()
    open(x, y)
  }

  return {
    begin(event, open) {
      touched.current = event.pointerType === 'touch'
      if (!touched.current) return
      cancel()
      const { clientX, clientY, pointerId } = event
      const moved = (later: PointerEvent) => {
        if (later.pointerId !== pointerId) return
        if (Math.hypot(later.clientX - clientX, later.clientY - clientY) > SLOP_PX) cancel()
      }
      const ended = (later: PointerEvent) => { if (later.pointerId === pointerId) cancel() }
      window.addEventListener('pointermove', moved, true)
      window.addEventListener('pointerup', ended, true)
      window.addEventListener('pointercancel', ended, true)
      watch.current = () => {
        window.removeEventListener('pointermove', moved, true)
        window.removeEventListener('pointerup', ended, true)
        window.removeEventListener('pointercancel', ended, true)
      }
      timer.current = window.setTimeout(() => {
        timer.current = null
        suppress.current = true
        cancel()
        navigator.vibrate?.(20)
        stamp(open, clientX, clientY)
      }, holdMs)
    },
    contextMenu(event, open) {
      event.preventDefault()
      event.stopPropagation()
      cancel()
      if (touched.current) suppress.current = true
      stamp(open, event.clientX, event.clientY)
    },
    keyboardMenu(event, open) {
      if (event.key !== 'ContextMenu' && !(event.key === 'F10' && event.shiftKey)) return false
      event.preventDefault()
      touched.current = false
      const box = event.currentTarget.getBoundingClientRect()
      stamp(open, box.left, box.bottom)
      return true
    },
    suppressClick(event) {
      if (!suppress.current) return
      suppress.current = false
      event.preventDefault()
      event.stopPropagation()
    },
    suppressMenuEcho(event) {
      if (!touched.current || performance.now() - openedAt.current >= TOUCH_ECHO_MS) return
      event.preventDefault()
      event.stopPropagation()
    },
  }
}
