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
  PAD_CENTER,
  padRingCount,
  padSlotKey,
  padSlotKeys,
  padWedgeCentreDeg,
  padWedgeCount,
  padWedgeName,
  railPadBanded,
  padWedgeUnit,
  parsePadSlotKey,
  type RailItem,
  type RailPadSlotKey,
  type RailPadTriggerMode,
} from './commandRail'
import { holdSoftKeyboard, restoreSoftKeyboard, softKeyboardDismissals, softKeyboardHolder } from './mobileKeyboard'
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
  band: (beyond: boolean) => void
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
        band: beyond => handlersRef.current?.band(beyond),
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

type Dial = {
  x: number
  y: number
  scale: number
  banded: boolean
  latch: RailPadLatch
  armed: boolean
  /** Past the outer band, which is what arms an `enter-repeat-far` slot's stream. */
  beyond: boolean
  visible: boolean
} | null

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
  const wedges = padWedgeCount(item.pad)
  const rings = padRingCount(item.pad)
  // Drawn with an outer band when a second ring of slots needs one, or when a slot repeats
  // beyond one. Same radii; the meaning is the slot's.
  const banded = railPadBanded(rings, slots.map(slot => slot.mode))
  const byKey = new Map(slots.map(slot => [slot.key, slot]))
  const centre = byKey.get(PAD_CENTER)
  /** The field holding the soft keyboard up when this press began, and the dismissal count
   *  at that moment, so the keyboard can be handed back when the gesture ends. */
  const keyboardRef = useRef<{ holder: HTMLElement | null; dismissals: number } | null>(null)

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
    // **The keyboard hold has to happen here, not on `mousedown`.**
    //
    // Every other rail chip acts on `click`, so its `onMouseDown` focus refusal has
    // necessarily already run. A pad acts on `pointermove`, and a touch that turns into a
    // drag delivers *no mouse events at all* - measured through CDP: a tap gives
    // `pointerdown, touchstart, touchend, mousedown, mouseup, click`, a drag gives only
    // `pointerdown, touchstart, touchend`. So on the one gesture the pad exists for, the
    // guard every other chip relies on never fires, nothing refuses focus, and Android drops
    // the keyboard.
    //
    // Capture-and-restore rather than refusal, because there is no event left to refuse:
    // the same shape `RailScroller` uses for its pan, which has the identical problem. The
    // dismissal count comes along so a *deliberate* dismissal during the gesture still wins.
    keyboardRef.current = { holder: softKeyboardHolder(), dismissals: softKeyboardDismissals() }
    const specs: Partial<Record<RailPadSlotKey, RailPadSlotSpec>> = {}
    for (const slot of slots) specs[slot.key] = { mode: slot.mode, disabled: slot.disabled }
    // Room above the finger, measured against the *visual* viewport. The fan opens upward,
    // so this is the one direction that can run out - a pad in a short pane near the top of
    // the window has less than the far ring's reach, and a boundary you cannot travel to is
    // a slot that does not exist.
    const view = railOverlayView()
    const roomAbove = Math.max(0, event.clientY - view.top - DIAL_EDGE_MARGIN_PX)
    const opened = controller.gesture.press(event.pointerId, event.clientX, event.clientY, {
      wedges,
      rings,
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
      // A distinct bump on arming the stream, because the finger is past the labels by then
      // and the only channel left is the one it can feel.
      band: crossed => {
        if (crossed) buzz(HAPTIC_ARM)
        setDial(current => current && { ...current, beyond: crossed })
      },
      // Torn down here rather than from the chip's own `pointerup`: by the time a real
      // gesture ends the finger is well off the chip, and that event belongs to whatever is
      // under it. The keyboard goes back on the same signal, and on a frame later so it
      // lands after the slot's own action has done whatever it does with focus.
      end: () => {
        closeDial()
        const held = keyboardRef.current
        keyboardRef.current = null
        if (held?.holder) requestAnimationFrame(() => restoreSoftKeyboard(held.holder, held.dismissals))
      },
    })
    // Client coordinates go in raw because the dial is portalled to `document.body`, which
    // is the one mount where they mean screen pixels. Rendering it inside the chip is not an
    // option: the rail's scroller sits between the pane's transform and the chip, and a
    // transformed ancestor makes that scroller clip even `position:fixed` descendants - the
    // dial would be cut off at the edge of the strip.
    setDial({
      x: event.clientX,
      y: event.clientY,
      scale: railPadScaleFor(banded, roomAbove),
      banded,
      latch: null,
      armed: false,
      beyond: false,
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

  /**
   * The keyboard route, which has no pointer and therefore no wedge to aim at.
   *
   * Number keys rather than arrows, because the wedge count is a choice: three arrows could
   * only ever address three of up to five wedges, and which three would depend on the pad.
   * `1` is the leftmost wedge, reading the dial the way it is drawn; a second ring continues
   * the count from where the first left off. Arrows stay as a shorthand for the three-wedge
   * case, where left/up/right *are* the wedges and the mapping is honest.
   */
  const keySlot = (key: string): RailPadSlotKey | null => {
    const digit = /^[1-9]$/.test(key) ? Number(key) - 1 : -1
    if (digit >= 0) {
      const ring = Math.floor(digit / wedges)
      const wedge = wedges - 1 - (digit % wedges)
      return ring < rings ? padSlotKey(ring, wedge) : null
    }
    if (wedges !== 3 || rings !== 1) return null
    return key === 'ArrowLeft' ? padSlotKey(0, 2)
      : key === 'ArrowUp' ? padSlotKey(0, 1)
        : key === 'ArrowRight' ? padSlotKey(0, 0) : null
  }

  // Drawn and read left to right, which is the reverse of the wedge index: wedge 0 is the
  // rightmost, because the fan's angles grow counter-clockwise from due east.
  const drawn = padSlotKeys(item.pad).filter(key => key !== PAD_CENTER)
  const populated = drawn.filter(key => byKey.has(key))
  const accessible = [
    modifierPrefix ? `${modifierPrefix} pad` : 'Pad',
    ...populated.map(key => {
      const at = parsePadSlotKey(key)!
      return `${padWedgeName(at.wedge, wedges, at.ring)}: ${byKey.get(key)?.label}`
    }),
    ...(centre ? [`Centre: ${centre.label}`] : []),
  ].join('. ')

  const bands = railPadBands(dial?.banded ?? banded, dial?.scale ?? 1)
  const innerOf = (ring: number) => ring > 0 ? bands.ring : bands.dead
  const outerOf = (ring: number) => ring > 0 || !Number.isFinite(bands.ring) ? bands.outer : bands.ring

  return <button
    ref={buttonRef}
    type="button"
    class={`${className || 'term-key'} rail-pad rail-pad-w${wedges} rail-pad-r${rings}${dial ? ' rail-pad-pressed' : ''}`}
    title={item.title || 'Drag a direction'}
    aria-label={accessible}
    // Still here for the *tap*, which does deliver a `mousedown`. The drag is covered by the
    // capture-and-restore in `beginPress`, because a drag delivers none.
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
      const key = keySlot(event.key)
      if (!key || !byKey.has(key)) return
      event.preventDefault()
      runSlot(key)
    }}
    onClick={() => {
      closeDial()
      // A press the gesture already answered - any drag at all, and a centre tap it fired
      // itself - leaves nothing for the click. Everything else is a plain tap.
      if (controller.gesture.consumeHandledClick()) return
      if (centre) runSlot(PAD_CENTER)
    }}
  >
    {content}
    {/* Populated wedges, marked inside the chip's own border. Drawn rather than laid out, so
        the chip is exactly the size it would be without them. One tick per wedge, angled the
        way that wedge points; a far-ring one sits inboard of its near partner, which is the
        only thing on the chip that says a wedge has two depths. */}
    <span class="rail-pad-marks" aria-hidden="true">
      {populated.map(key => {
        const at = parsePadSlotKey(key)!
        const unit = padWedgeUnit(at.wedge, wedges)
        const reach = at.ring > 0 ? 0.62 : 0.86
        return <span
          key={key}
          class="rail-pad-mark"
          style={{
            left: `${50 + unit.x * reach * 50}%`,
            top: `${50 + unit.y * reach * 50}%`,
            transform: `translate(-50%,-50%) rotate(${-padWedgeCentreDeg(at.wedge, wedges) - 45}deg)`,
          }}
        />
      })}
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
        {drawn.map(key => {
          const slot = byKey.get(key)
          const { ring, wedge } = parsePadSlotKey(key)!
          const { from, to } = railPadWedgeBounds(wedge, wedges)
          const inner = innerOf(ring)
          const outer = outerOf(ring)
          const active = dial.latch === key
          // A wedge that repeats beyond the band draws its label inside the near part and
          // marks the outer part separately, because the two halves do different things -
          // one send, or a stream. Every other wedge fills its whole band.
          const streams = slot?.mode === 'enter-repeat-far' && Number.isFinite(bands.ring)
          const labelOuter = streams ? bands.ring : outer
          const at = polar(inner + (labelOuter - inner) * LABEL_AT, railPadWedgeCentre(wedge, wedges))
          const label = slot ? (modifierPrefix ? `${modifierPrefix}+${slot.label}` : slot.label) : ''
          return <g
            key={key}
            class={`rail-pad-wedge${active ? ' rail-pad-wedge-active' : ''}`
              + `${active && dial.armed ? ' rail-pad-wedge-armed' : ''}`
              + `${!slot || slot.disabled ? ' rail-pad-wedge-off' : ''}`
              + `${slot?.mode === 'release' ? ' rail-pad-wedge-release' : ''}`}
          >
            <path d={wedgePath(inner, labelOuter, from, to)}/>
            {streams && <path
              class={`rail-pad-band${active && dial.beyond ? ' rail-pad-band-live' : ''}`}
              d={wedgePath(bands.ring, outer, from, to)}
            />}
            {label && <text
              x={at.x}
              y={at.y}
              // Clamped into the viewport rather than the wedge clamped: the hitbox is
              // angular and costs nothing where it cannot be seen, but a label off the edge
              // is the one part of the drawing that mattered.
              transform={`translate(${labelShift(dial.x + at.x, label.length)} 0)`}
            >{label}</text>}
            {streams && (() => {
              const mark = polar(bands.ring + (outer - bands.ring) * LABEL_AT, railPadWedgeCentre(wedge, wedges))
              return <text
                class="rail-pad-band-mark"
                x={mark.x}
                y={mark.y}
                transform={`translate(${labelShift(dial.x + mark.x, 3)} 0)`}
              >⋯</text>
            })()}
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
