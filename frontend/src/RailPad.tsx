// The command rail's pad chip: the element, its pointer listeners, the petals it draws
// while pressed, and the haptics. The rules it obeys live in `railPad.ts`.
//
// Its own module rather than JSX inside TerminalPane for the same reason `RailRepeatKey`
// is: the gesture *is* this button, and a gesture is only worth trusting once real
// touches have been driven at it through a real rail
// (`test/renderer/command-rail-pad.spec.ts`). A harness that re-implemented the button
// would be testing a copy of the thing that can break.

import type { ComponentChildren } from 'preact'
import { createPortal } from 'preact/compat'
import { useEffect, useRef, useState } from 'preact/hooks'

import {
  padDirectionUnit,
  padDirections,
  RAIL_PAD_DIRECTION_LABELS,
  type RailItem,
  type RailPadDirection,
  type RailPadSlotKey,
  type RailPadTriggerMode,
} from './commandRail'
import { holdSoftKeyboard } from './mobileKeyboard'
import {
  createRailPadGesture,
  RAIL_PAD_ENTER_RADIUS_PX,
  RAIL_PAD_PETAL_DELAY_MS,
  railPadRadius,
  type RailPadGesture,
  type RailPadLatch,
  type RailPadSlotSpec,
} from './railPadGesture'
import { railOverlayView } from './railOverlayPlacement'

/** How far from the press point a petal is drawn at full radius. Purely visual: the
 *  gesture commits at `RAIL_PAD_ENTER_RADIUS_PX`, and a petal sits further out so a
 *  thumb does not cover the label it is aiming at. A squeezed direction's petal is
 *  pulled in by the same ratio its radius was, so the drawing never lies. */
const PETAL_RADIUS_PX = 46
/** Kept clear below the press point so a petal is not drawn under the screen edge. */
const PETAL_EDGE_MARGIN_PX = 4

/** Entry tick, the distinct double-bump a `release` slot arms with, and the near-silent
 *  tick a repetition carries. Most of what makes the control feel like hardware, and the
 *  only channel that reaches a finger already covering the label. */
const HAPTIC_ENTER = 8
const HAPTIC_ARM = [3, 24, 6]
const HAPTIC_REPEAT = 3

const buzz = (pattern: number | number[]) => { navigator.vibrate?.(pattern) }

/** One bound direction, resolved by the host against the live session. */
export interface RailPadSlotView {
  key: RailPadSlotKey
  /** Catalog id, for keys and diagnostics. */
  itemId: string
  label: string
  title: string
  mode: RailPadTriggerMode
  disabled: boolean
  run: (anchor: HTMLElement | null) => void
}

interface RailPadHandlers {
  fire: (slot: RailPadSlotKey) => void
  latch: (slot: RailPadLatch, detail: { armed: boolean }) => void
  end: () => void
}

export interface RailPadController {
  gesture: RailPadGesture
  /** Take over the shared gesture for this pad's press, and follow it at the window. */
  begin(handlers: RailPadHandlers): void
}

/**
 * One controller per rail, not per chip.
 *
 * Two pads must never repeat at once, whatever takes the window away has to be able to
 * stop whichever one is, and `RailStrip` renders every chip a second time inside its
 * overflow popover - so two live instances of the same pad genuinely coexist and must
 * share one press.
 *
 * The press is followed at the *window* rather than on the chip, for the reason
 * `RailRepeatKey` documents: the rail's pan takes pointer capture as soon as the same
 * touch starts scrolling, and capture retargets every later pointer event, so a
 * chip-local `pointermove` would never see the travel it has to answer for. The
 * listeners exist only while a press is open.
 */
export function useRailPad(resetKey: string): RailPadController {
  const handlersRef = useRef<RailPadHandlers | null>(null)
  const stateRef = useRef<{ controller: RailPadController; unwatch: () => void } | null>(null)
  if (!stateRef.current) {
    let detach: (() => void) | null = null
    const unwatch = () => { detach?.(); detach = null }
    const gesture = createRailPadGesture(
      {
        fire: slot => handlersRef.current?.fire(slot),
        latch: (slot, detail) => handlersRef.current?.latch(slot, detail),
        end: () => {
          const handlers = handlersRef.current
          handlersRef.current = null
          handlers?.end()
        },
      },
      (callback, delayMs) => window.setTimeout(callback, delayMs),
      timer => window.clearTimeout(timer),
    )
    const watch = () => {
      unwatch()
      const move = (event: PointerEvent) => { gesture.move(event.pointerId, event.clientX, event.clientY) }
      const end = (event: PointerEvent) => { if (gesture.release(event.pointerId)) unwatch() }
      const cancel = () => { gesture.cancel(); unwatch() }
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', end)
      window.addEventListener('pointercancel', cancel)
      detach = () => {
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', end)
        window.removeEventListener('pointercancel', cancel)
      }
    }
    stateRef.current = {
      controller: {
        gesture,
        begin(handlers) { handlersRef.current = handlers; watch() },
      },
      unwatch,
    }
  }
  const { controller, unwatch } = stateRef.current
  useEffect(() => {
    const cancel = () => { controller.gesture.cancel(); unwatch() }
    window.addEventListener('blur', cancel)
    document.addEventListener('visibilitychange', cancel)
    return () => {
      window.removeEventListener('blur', cancel)
      document.removeEventListener('visibilitychange', cancel)
      cancel()
    }
  }, [resetKey])
  return controller
}

export interface RailPadProps {
  controller: RailPadController
  item: RailItem
  /** Bound slots only. An unbound direction is simply absent and reads as dead. */
  slots: readonly RailPadSlotView[]
  className?: string
  /** The chip's own face: label or icon, resolved by the host like any other chip. */
  content: ComponentChildren
  /** Modifier prefix currently applying, e.g. `Ctrl`. Petals show it so a live modifier
   *  is legible on the four things it is about to change. */
  modifierPrefix?: string
}

type Petals = { x: number; y: number; latch: RailPadLatch; armed: boolean; visible: boolean } | null

/**
 * Tap the centre, or drag a direction.
 *
 * The chip refuses focus on `mousedown` like the rest of the rail, so a press never
 * lowers an open soft keyboard, while `tabIndex` keeps it reachable by Tab. Keyboard
 * activation is the one path with no pointer: Enter and Space arrive as an ordinary
 * click and run the centre, and the four directions have real keys of their own - the
 * arrows on a cardinal pad, and the navigation cluster's own spatial arrangement
 * (Home/PageUp over End/PageDown) on a diagonal one.
 */
export function RailPad({ controller, item, slots, className, content, modifierPrefix }: RailPadProps) {
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [petals, setPetals] = useState<Petals>(null)
  const petalTimer = useRef<number | null>(null)
  const orientation = item.pad?.orientation ?? 'cardinal'
  const directions = padDirections(orientation)
  const byKey = new Map(slots.map(slot => [slot.key, slot]))
  const centre = byKey.get('center')
  const clearanceRef = useRef(Infinity)

  useEffect(() => () => { if (petalTimer.current !== null) window.clearTimeout(petalTimer.current) }, [])

  const closePetals = () => {
    if (petalTimer.current !== null) { window.clearTimeout(petalTimer.current); petalTimer.current = null }
    setPetals(null)
  }

  const runSlot = (key: RailPadSlotKey) => {
    const slot = byKey.get(key)
    if (!slot || slot.disabled) return
    slot.run(buttonRef.current)
  }

  const beginPress = (event: PointerEvent) => {
    const specs: Partial<Record<RailPadSlotKey, RailPadSlotSpec>> = {}
    for (const slot of slots) specs[slot.key] = { mode: slot.mode, disabled: slot.disabled }
    // Room under the finger, measured against the *visual* viewport so an open soft
    // keyboard counts as the floor it is. This is what squeezes a descending slot's
    // radius rather than leaving it unreachable at the bottom of a phone.
    const view = railOverlayView()
    clearanceRef.current = Math.max(0, view.top + view.height - event.clientY - PETAL_EDGE_MARGIN_PX)
    const opened = controller.gesture.press(event.pointerId, event.clientX, event.clientY, {
      orientation,
      slots: specs,
      clearanceBelowPx: clearanceRef.current,
    })
    if (!opened) return
    let fired = false
    controller.begin({
      fire: key => {
        buzz(fired ? HAPTIC_REPEAT : HAPTIC_ENTER)
        fired = true
        runSlot(key)
      },
      latch: (slot, detail) => {
        fired = false
        if (slot && detail.armed) buzz(HAPTIC_ARM)
        setPetals(current => current && { ...current, latch: slot, armed: detail.armed })
      },
      // Torn down here rather than from the chip's own `pointerup`: by the time a real
      // gesture ends, the finger is well off the chip and that event belongs to whatever
      // is under it, so the chip would never hear about the press it started.
      end: closePetals,
    })
    // Client coordinates go in raw because the petals are portalled to `document.body`,
    // which is the one mount where they mean screen pixels. Rendering them inside the
    // chip is not an option: the rail's scroller sits between the pane's transform and
    // the chip, and a transformed ancestor makes that scroller clip even `position:fixed`
    // descendants - the petals would be cut off at the edge of the strip. The drop-ups
    // dodge the same trap by being rendered at pane level instead.
    setPetals({ x: event.clientX, y: event.clientY, latch: null, armed: false, visible: false })
    // Cosmetic only. The gesture has been live since the line above; this decides
    // nothing except whether the operator is shown the map before they finish.
    if (petalTimer.current !== null) window.clearTimeout(petalTimer.current)
    petalTimer.current = window.setTimeout(() => {
      petalTimer.current = null
      setPetals(current => current && { ...current, visible: true })
    }, RAIL_PAD_PETAL_DELAY_MS)
  }

  const keyDirection = (key: string): RailPadDirection | null => {
    if (orientation === 'cardinal') {
      return key === 'ArrowUp' ? 'up' : key === 'ArrowDown' ? 'down'
        : key === 'ArrowLeft' ? 'left' : key === 'ArrowRight' ? 'right' : null
    }
    return key === 'Home' ? 'upLeft' : key === 'PageUp' ? 'upRight'
      : key === 'End' ? 'downLeft' : key === 'PageDown' ? 'downRight' : null
  }

  const populated = directions.filter(direction => byKey.has(direction))
  const accessible = [
    modifierPrefix ? `${modifierPrefix} pad` : 'Pad',
    ...populated.map(direction => `${RAIL_PAD_DIRECTION_LABELS[direction]}: ${byKey.get(direction)?.label}`),
  ].join('. ')

  return <button
    ref={buttonRef}
    type="button"
    class={`${className || 'term-key'} rail-pad rail-pad-${orientation}${petals ? ' rail-pad-pressed' : ''}`}
    title={item.title || 'Drag a direction'}
    aria-label={accessible}
    onMouseDown={event => { event.preventDefault(); holdSoftKeyboard(event) }}
    onContextMenu={event => event.preventDefault()}
    onPointerDown={event => {
      if (!event.isPrimary || event.button !== 0) return
      beginPress(event as unknown as PointerEvent)
    }}
    onPointerUp={closePetals}
    onPointerCancel={closePetals}
    onLostPointerCapture={closePetals}
    onKeyDown={event => {
      const direction = keyDirection(event.key)
      if (!direction || !byKey.has(direction)) return
      event.preventDefault()
      runSlot(direction)
    }}
    onClick={() => {
      closePetals()
      // A press the gesture already answered - any drag at all, and a centre tap it
      // fired itself - leaves nothing for the click. Everything else is a plain tap.
      if (controller.gesture.consumeHandledClick()) return
      if (centre) runSlot('center')
    }}
  >
    {content}
    {/* Populated directions, marked inside the chip's own border. Drawn rather than
        laid out, so the chip is exactly the size it would be without them. */}
    <span class="rail-pad-marks" aria-hidden="true">
      {populated.map(direction => <span key={direction} class={`rail-pad-mark rail-pad-mark-${direction}`}/>)}
    </span>
    {petals && createPortal(<span
      class={`rail-pad-petals${petals.visible ? ' rail-pad-petals-shown' : ''}`}
      style={{ left: `${Math.round(petals.x)}px`, top: `${Math.round(petals.y)}px` }}
      aria-hidden="true"
    >
      {populated.map(direction => {
        const slot = byKey.get(direction)
        if (!slot) return null
        const unit = padDirectionUnit(direction)
        // The petal sits at the same fraction of full reach that this direction's
        // threshold sits at, so a squeezed downward slot is drawn closer in.
        const reach = PETAL_RADIUS_PX * (railPadRadius(direction, clearanceRef.current) / RAIL_PAD_ENTER_RADIUS_PX)
        const active = petals.latch === direction
        return <span
          key={direction}
          class={`rail-pad-petal${active ? ' rail-pad-petal-active' : ''}${active && petals.armed ? ' rail-pad-petal-armed' : ''}${slot.disabled ? ' rail-pad-petal-off' : ''}${slot.mode === 'release' ? ' rail-pad-petal-release' : ''}`}
          style={{ transform: `translate(-50%, -50%) translate(${Math.round(unit.x * reach)}px, ${Math.round(unit.y * reach)}px)` }}
        >{modifierPrefix ? `${modifierPrefix}+${slot.label}` : slot.label}</span>
      })}
    </span>, document.body)}
  </button>
}
