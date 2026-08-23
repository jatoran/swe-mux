// The command rail's pad chip: the element, its pointer listeners, the dial it draws while
// pressed, and the haptics. The rules it obeys live in `railPadGesture.ts`.
//
// Its own module rather than JSX inside TerminalPane for the same reason `RailRepeatKey`
// is: the gesture *is* this button, and a gesture is only worth trusting once real touches
// have been driven at it through a real rail (`test/renderer/command-rail-pad.spec.ts`). A
// harness that re-implemented the button would be testing a copy of the thing that can break.

import type { ComponentChildren } from 'preact'
import { createPortal } from 'preact/compat'
import { useEffect, useRef, useState } from 'preact/hooks'

import {
  padDirections,
  padRingOf,
  padSectorCount,
  RAIL_PAD_DIRECTION_LABELS,
  type RailItem,
  type RailPadDirection,
  type RailPadSlotKey,
  type RailPadTriggerMode,
} from './commandRail'
import { holdSoftKeyboard } from './mobileKeyboard'
import {
  createRailPadGesture,
  RAIL_PAD_DIAL_DELAY_MS,
  railPadBands,
  railPadScaleFor,
  railPadWedgeBounds,
  railPadWedgeCentre,
  type RailPadGesture,
  type RailPadLatch,
  type RailPadSlotSpec,
} from './railPadGesture'
import { railOverlayView } from './railOverlayPlacement'

/** Kept clear of the viewport edge when the dial is squeezed to fit. */
const DIAL_EDGE_MARGIN_PX = 8
/** Label distance into a band, as a fraction of the way from its inner to its outer edge.
 *  Past the middle, because the outer arc is longer and the text reads better out there. */
const LABEL_AT = 0.55
/** How far a label may sit from the viewport's own edges before it is pulled back in.
 *  The wedge itself is free to run off screen - its hitbox is angular and costs nothing
 *  where it cannot be seen - but a label that did would be the one part that mattered. */
const LABEL_EDGE_PX = 10
/** Half a character, at the label's own size. The labels are centre-anchored, so clamping
 *  their *centre* to the viewport still lets half the word hang off; the clamp has to know
 *  how wide the word is, and a monospace face makes that a multiplication. */
const LABEL_HALF_CHAR_PX = 4.6

/** Entry tick, the distinct double-bump a `release` slot arms with, and the near-silent tick
 *  a repetition carries. Most of what makes the control feel like hardware, and the only
 *  channel that reaches a finger already covering the wedge. */
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
 * Two pads must never repeat at once, whatever takes the window away has to be able to stop
 * whichever one is, and `RailStrip` renders every chip a second time inside its overflow
 * popover - so two live instances of the same pad genuinely coexist and must share one press.
 *
 * The press is followed at the *window* rather than on the chip, for the reason
 * `RailRepeatKey` documents: the rail's pan takes pointer capture as soon as the same touch
 * starts scrolling, and capture retargets every later pointer event, so a chip-local
 * `pointermove` would never see the travel it has to answer for. The listeners exist only
 * while a press is open.
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
  /** Modifier prefix currently applying, e.g. `Ctrl`. The dial shows it, so a live modifier
   *  is legible on the things it is about to change. */
  modifierPrefix?: string
}

type Dial = { x: number; y: number; scale: number; latch: RailPadLatch; armed: boolean; visible: boolean } | null

/** A point on the dial, in its own local coordinates: the press is the origin, `angle` is
 *  `railPadAngle` degrees, and `y` is flipped because the fan opens upward. */
const polar = (radius: number, angle: number) => ({
  x: radius * Math.cos(angle * Math.PI / 180),
  y: -radius * Math.sin(angle * Math.PI / 180),
})

/**
 * One wedge, as an annulus sector between two radii and two angles.
 *
 * Drawn from the same numbers the gesture resolves against, which is the point: the wedge
 * *is* the hitbox, so a dial that showed a boundary the gesture did not have would be
 * lying at exactly the moment the operator is trusting it.
 */
function wedgePath(inner: number, outer: number, from: number, to: number): string {
  const large = to - from > 180 ? 1 : 0
  const a = polar(inner, from)
  const b = polar(outer, from)
  const c = polar(outer, to)
  const d = polar(inner, to)
  // Increasing `railPadAngle` runs counter-clockwise on screen, which is sweep-flag 0 going
  // out along the outer arc and 1 coming back along the inner one.
  return `M ${a.x} ${a.y} L ${b.x} ${b.y} A ${outer} ${outer} 0 ${large} 0 ${c.x} ${c.y}`
    + ` L ${d.x} ${d.y} A ${inner} ${inner} 0 ${large} 1 ${a.x} ${a.y} Z`
}

/**
 * Tap the centre, or drag a wedge.
 *
 * The chip refuses focus on `mousedown` like the rest of the rail, so a press never lowers
 * an open soft keyboard, while `tabIndex` keeps it reachable by Tab. Keyboard activation is
 * the one path with no pointer: Enter and Space arrive as an ordinary click and run the
 * centre, and the directions have real keys of their own - the three arrows on a cardinal
 * pad, and the navigation cluster's own spatial arrangement (Home/PageUp near, End/PageDown
 * far) on a diagonal one.
 */
export function RailPad({ controller, item, slots, className, content, modifierPrefix }: RailPadProps) {
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [dial, setDial] = useState<Dial>(null)
  const dialTimer = useRef<number | null>(null)
  const orientation = item.pad?.orientation ?? 'cardinal'
  const directions = padDirections(orientation)
  const sectors = padSectorCount(orientation)
  const byKey = new Map(slots.map(slot => [slot.key, slot]))
  const centre = byKey.get('center')

  useEffect(() => () => { if (dialTimer.current !== null) window.clearTimeout(dialTimer.current) }, [])

  const closeDial = () => {
    if (dialTimer.current !== null) { window.clearTimeout(dialTimer.current); dialTimer.current = null }
    setDial(null)
  }

  const runSlot = (key: RailPadSlotKey) => {
    const slot = byKey.get(key)
    if (!slot || slot.disabled) return
    slot.run(buttonRef.current)
  }

  const beginPress = (event: PointerEvent) => {
    const specs: Partial<Record<RailPadSlotKey, RailPadSlotSpec>> = {}
    for (const slot of slots) specs[slot.key] = { mode: slot.mode, disabled: slot.disabled }
    // Room above the finger, measured against the *visual* viewport. The fan opens upward,
    // so this is the one direction that can run out - a pad in a short pane near the top of
    // the window has less than the far ring's reach, and a boundary you cannot travel to is
    // a slot that does not exist.
    const view = railOverlayView()
    const roomAbove = Math.max(0, event.clientY - view.top - DIAL_EDGE_MARGIN_PX)
    const opened = controller.gesture.press(event.pointerId, event.clientX, event.clientY, {
      orientation,
      slots: specs,
      roomAbovePx: roomAbove,
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
        setDial(current => current && { ...current, latch: slot, armed: detail.armed })
      },
      // Torn down here rather than from the chip's own `pointerup`: by the time a real
      // gesture ends the finger is well off the chip, and that event belongs to whatever is
      // under it.
      end: closeDial,
    })
    // Client coordinates go in raw because the dial is portalled to `document.body`, which
    // is the one mount where they mean screen pixels. Rendering it inside the chip is not an
    // option: the rail's scroller sits between the pane's transform and the chip, and a
    // transformed ancestor makes that scroller clip even `position:fixed` descendants - the
    // dial would be cut off at the edge of the strip.
    setDial({
      x: event.clientX,
      y: event.clientY,
      scale: railPadScaleFor(orientation, roomAbove),
      latch: null,
      armed: false,
      visible: false,
    })
    // Cosmetic only. The gesture has been live since the line above; this decides nothing
    // except whether the operator is shown the map before they finish.
    if (dialTimer.current !== null) window.clearTimeout(dialTimer.current)
    dialTimer.current = window.setTimeout(() => {
      dialTimer.current = null
      setDial(current => current && { ...current, visible: true })
    }, RAIL_PAD_DIAL_DELAY_MS)
  }

  const keyDirection = (key: string): RailPadDirection | null => {
    if (orientation === 'cardinal') {
      return key === 'ArrowUp' ? 'up' : key === 'ArrowLeft' ? 'left' : key === 'ArrowRight' ? 'right' : null
    }
    return key === 'Home' ? 'upLeft' : key === 'PageUp' ? 'upRight'
      : key === 'End' ? 'upLeftFar' : key === 'PageDown' ? 'upRightFar' : null
  }

  const populated = directions.filter(direction => byKey.has(direction))
  const accessible = [
    modifierPrefix ? `${modifierPrefix} pad` : 'Pad',
    ...populated.map(direction => `${RAIL_PAD_DIRECTION_LABELS[direction]}: ${byKey.get(direction)?.label}`),
  ].join('. ')

  const bands = railPadBands(orientation, dial?.scale ?? 1)
  const outerOf = (ring: 'near' | 'far') => ring === 'far' || !Number.isFinite(bands.ring) ? bands.outer : bands.ring
  const innerOf = (ring: 'near' | 'far') => ring === 'far' ? bands.ring : bands.dead

  return <button
    ref={buttonRef}
    type="button"
    class={`${className || 'term-key'} rail-pad rail-pad-${orientation}${dial ? ' rail-pad-pressed' : ''}`}
    title={item.title || 'Drag a direction'}
    aria-label={accessible}
    onMouseDown={event => { event.preventDefault(); holdSoftKeyboard(event) }}
    onContextMenu={event => event.preventDefault()}
    onPointerDown={event => {
      if (!event.isPrimary || event.button !== 0) return
      beginPress(event as unknown as PointerEvent)
    }}
    onPointerUp={closeDial}
    onPointerCancel={closeDial}
    onLostPointerCapture={closeDial}
    onKeyDown={event => {
      const direction = keyDirection(event.key)
      if (!direction || !byKey.has(direction)) return
      event.preventDefault()
      runSlot(direction)
    }}
    onClick={() => {
      closeDial()
      // A press the gesture already answered - any drag at all, and a centre tap it fired
      // itself - leaves nothing for the click. Everything else is a plain tap.
      if (controller.gesture.consumeHandledClick()) return
      if (centre) runSlot('center')
    }}
  >
    {content}
    {/* Populated directions, marked inside the chip's own border. Drawn rather than laid
        out, so the chip is exactly the size it would be without them. */}
    <span class="rail-pad-marks" aria-hidden="true">
      {populated.map(direction => <span key={direction} class={`rail-pad-mark rail-pad-mark-${direction}`}/>)}
    </span>
    {dial && createPortal(<div
      class={`rail-pad-dial${dial.visible ? ' rail-pad-dial-shown' : ''}`}
      aria-hidden="true"
    >
      {/* A wash over the workspace, so the wedges read as one surface rather than as
          translucent shapes competing with whatever the terminal happens to be drawing. */}
      <div class="rail-pad-dial-scrim"/>
      <svg
        class="rail-pad-dial-svg"
        style={{ left: `${Math.round(dial.x)}px`, top: `${Math.round(dial.y)}px` }}
        width={bands.outer * 2}
        height={bands.outer * 2}
        viewBox={`${-bands.outer} ${-bands.outer} ${bands.outer * 2} ${bands.outer * 2}`}
      >
        {directions.map((direction, position) => {
          const slot = byKey.get(direction)
          const index = position % sectors
          const ring = padRingOf(direction)
          const { from, to } = railPadWedgeBounds(orientation, index)
          const inner = innerOf(ring)
          const outer = outerOf(ring)
          const active = dial.latch === direction
          const at = polar(inner + (outer - inner) * LABEL_AT, railPadWedgeCentre(orientation, index))
          const label = slot ? (modifierPrefix ? `${modifierPrefix}+${slot.label}` : slot.label) : ''
          return <g
            key={direction}
            class={`rail-pad-wedge${active ? ' rail-pad-wedge-active' : ''}`
              + `${active && dial.armed ? ' rail-pad-wedge-armed' : ''}`
              + `${!slot || slot.disabled ? ' rail-pad-wedge-off' : ''}`
              + `${slot?.mode === 'release' ? ' rail-pad-wedge-release' : ''}`}
          >
            <path d={wedgePath(inner, outer, from, to)}/>
            {label && <text
              x={at.x}
              y={at.y}
              // Clamped into the viewport rather than the wedge clamped: the hitbox is
              // angular and costs nothing where it cannot be seen, but a label off the edge
              // is the one part of the drawing that mattered.
              transform={`translate(${labelShift(dial.x + at.x, label.length)} 0)`}
            >{label}</text>}
          </g>
        })}
        <circle class="rail-pad-dial-hub" r={bands.dead}/>
      </svg>
    </div>, document.body)}
  </button>
}

/** How far a label has to slide along x to keep its whole width on screen. Zero for every
 *  label that already fits, which is nearly all of them. */
function labelShift(screenX: number, characters: number): number {
  const half = characters * LABEL_HALF_CHAR_PX
  const low = LABEL_EDGE_PX + half
  const high = Math.max(low, window.innerWidth - LABEL_EDGE_PX - half)
  return Math.min(Math.max(screenX, low), high) - screenX
}
