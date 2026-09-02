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
  end: (detail: { standing: boolean }) => void
}

export interface RailPadController {
  gesture: RailPadGesture
  /** Take over the shared gesture for this pad's press, and follow it at the window. */
  begin(handlers: RailPadHandlers): void
  /** Take it over *without* following a pointer, for the keyboard route: a dial opened by
   *  Enter has no pointer stream, and the press that eventually touches it brings its own. */
  bind(handlers: RailPadHandlers): void
}

/**
 * One controller per rail, not per chip.
 *
 * Two pads must never repeat at once, whatever takes the window away has to be able to stop
 * whichever one is, and `RailStrip` renders every chip a second time inside its overflow
 * popover - so two live instances of the same pad genuinely coexist and must share one press.
 *
 * That second instance is also why the standing dial needs no ownership token: the handlers
 * stay bound to whichever instance opened it, so exactly one of the two ever holds dial state
 * and exactly one ever renders the portal. A token would be a second answer to a question
 * `handlersRef` already answers.
 *
 * The press is followed at the *window* rather than on the chip, for the reason
 * `RailRepeatKey` documents: the rail's pan takes pointer capture as soon as the same touch
 * starts scrolling, and capture retargets every later pointer event, so a chip-local
 * `pointermove` would never see the travel it has to answer for. The listeners exist only
 * while a press is open - a standing dial has no pointer to follow, and takes its input from
 * its own surface instead.
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
        // The handlers are released only when the dial goes with the press. A dial left
        // standing still has to be told when it closes, and the instance that opened it is
        // the one that has to hear it.
        end: detail => {
          const handlers = handlersRef.current
          if (!detail.standing) handlersRef.current = null
          handlers?.end(detail)
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
        bind(handlers) { handlersRef.current = handlers },
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
  /** This chip's position in the *stored* row, published as `data-rail-slot` so an arrange
   *  drag can translate a hit test back into a config index. A live rail draws a filtered
   *  projection, so its rendered order is not its stored order (`railArrange.ts`). */
  slot?: number
  /** The occurrence key, published as `data-reorder-id`: unique per placement, because the
   *  same Action may legitimately sit in two rows and twice within one. */
  reorderId?: string
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
  /** The dial outlived its press, opened by a tap. It takes pointer events, dismisses on
   *  Escape, and is announced - none of which a transient dial does. */
  standing: boolean
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
export function RailPad({ controller, item, slots, className, content, modifierPrefix, slot, reorderId }: RailPadProps) {
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [dial, setDial] = useState<Dial>(null)
  const dialTimer = useRef<number | null>(null)
  const wedges = padWedgeCount(item.pad)
  const rings = padRingCount(item.pad)
  // Drawn with an outer band when a second ring of slots needs one, or when a slot repeats
  // beyond one. Same radii; the meaning is the slot's.
  const banded = railPadBanded(rings, slots.map(slot => slot.mode))
  const byKey = new Map(slots.map(slot => [slot.key, slot]))
  /** The field holding the soft keyboard up when this press began, and the dismissal count
   *  at that moment, so the keyboard can be handed back when the gesture ends. */
  const keyboardRef = useRef<{ holder: HTMLElement | null; dismissals: number } | null>(null)

  useEffect(() => () => { if (dialTimer.current !== null) window.clearTimeout(dialTimer.current) }, [])

  const closeDial = () => {
    if (dialTimer.current !== null) { window.clearTimeout(dialTimer.current); dialTimer.current = null }
    setDial(null)
  }

  /** Tear the dial down only if the gesture did not leave it standing. The chip's own
   *  `pointerup` runs on every press, including the tap that just opened one. */
  const closeUnlessStanding = () => { if (!controller.gesture.peek().standing) closeDial() }

  const dismiss = () => { controller.gesture.dismiss() }

  // A standing dial is an overlay level, so it takes the two dismissals every other overlay
  // in the rail takes (`RailDropup`): Escape, and anything that moves the window out from
  // under it. Its own surface covers the viewport, so an outside *tap* needs no listener -
  // there is no outside. Resize and scroll are dismissals rather than re-placements because
  // the dial is pinned to a press that has already ended: there is no live anchor to follow.
  useEffect(() => {
    if (!dial?.standing) return
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); dismiss() } }
    window.addEventListener('keydown', key)
    window.addEventListener('resize', dismiss)
    window.visualViewport?.addEventListener('resize', dismiss)
    return () => {
      window.removeEventListener('keydown', key)
      window.removeEventListener('resize', dismiss)
      window.visualViewport?.removeEventListener('resize', dismiss)
    }
  }, [dial?.standing])

  const runSlot = (key: RailPadSlotKey) => {
    const slot = byKey.get(key)
    if (!slot || slot.disabled) return
    slot.run(buttonRef.current)
  }

  const slotSpecs = (): Partial<Record<RailPadSlotKey, RailPadSlotSpec>> => {
    const specs: Partial<Record<RailPadSlotKey, RailPadSlotSpec>> = {}
    for (const slot of slots) specs[slot.key] = { mode: slot.mode, disabled: slot.disabled }
    return specs
  }

  /** The dial's own scale for a fan opened at `clientY`, and the room it was computed from.
   *  Room above is measured against the *visual* viewport: the fan opens upward, so this is
   *  the one direction that can run out, and a boundary you cannot travel to is a slot that
   *  does not exist. */
  const fitAbove = (clientY: number) => {
    const roomAbove = Math.max(0, clientY - railOverlayView().top - DIAL_EDGE_MARGIN_PX)
    return { roomAbove, scale: railPadScaleFor(banded, roomAbove) }
  }

  const padHandlers = () => {
    let fired = false
    return {
      fire: (key: RailPadSlotKey) => {
        buzz(fired ? HAPTIC_REPEAT : HAPTIC_ENTER)
        fired = true
        runSlot(key)
      },
      latch: (slot: RailPadLatch, detail: { armed: boolean }) => {
        fired = false
        if (slot && detail.armed) buzz(HAPTIC_ARM)
        setDial(current => current && { ...current, latch: slot, armed: detail.armed })
      },
      // A distinct bump on arming the stream, because the finger is past the labels by then
      // and the only channel left is the one it can feel.
      band: (crossed: boolean) => {
        if (crossed) buzz(HAPTIC_ARM)
        setDial(current => current && { ...current, beyond: crossed })
      },
      // Torn down here rather than from the chip's own `pointerup`: by the time a real
      // gesture ends the finger is well off the chip, and that event belongs to whatever is
      // under it. The keyboard goes back on the same signal, and on a frame later so it
      // lands after the slot's own action has done whatever it does with focus.
      //
      // A dial the gesture left *standing* survives its press by definition, so only its
      // standing flag is raised here - and it is shown outright rather than waiting on the
      // draw delay, because the tap that opened it was a request to see it.
      end: (detail: { standing: boolean }) => {
        if (detail.standing) setDial(current => current && { ...current, standing: true, visible: true })
        else closeDial()
        const held = keyboardRef.current
        keyboardRef.current = null
        if (held?.holder) requestAnimationFrame(() => restoreSoftKeyboard(held.holder, held.dismissals))
      },
    }
  }

  /**
   * Enter or Space on a focused chip, which is the one route with no pointer behind it at all.
   *
   * It opens the dial rather than synthesising a press, because a press is a thing a pointer
   * does and there is none: the fan is placed over the chip's own centre and simply stands
   * there. Handlers are bound without the window listeners for the same reason - there is no
   * pointer stream to follow until somebody touches the dial, and that press binds its own.
   */
  const openFromKeyboard = () => {
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect || !slots.length) return
    const x = rect.left + rect.width / 2
    const y = rect.top + rect.height / 2
    const { roomAbove, scale } = fitAbove(y)
    controller.bind(padHandlers())
    if (!controller.gesture.open(x, y, { wedges, rings, slots: slotSpecs(), roomAbovePx: roomAbove })) return
    setDial({
      x,
      y: y - railPadBands(banded, scale).lift,
      scale,
      banded,
      latch: null,
      armed: false,
      beyond: false,
      visible: true,
      standing: true,
    })
  }

  const beginPress = (event: PointerEvent, adoptStanding = false) => {
    // **The keyboard hold has to happen here, not on `mousedown`.**
    //
    // Every other rail chip acts on `click`, so its `onMouseDown` focus refusal has
    // necessarily already run. A pad acts on `pointermove`, and a touch that turns into a
    // drag delivers *no mouse events at all* - measured through CDP: a tap gives
    // `pointerdown, touchstart, touchend, mousedown, mouseup, click`, a drag gives only
    // `pointerdown, touchstart, touchend`. The ordinary mouse guard therefore cannot protect
    // the gesture the pad exists for.
    //
    // A pad is the exception to the ordinary rail rule that `pointerdown` cannot be
    // cancelled: its touch path is the gesture itself, including the tap that opens a
    // standing dial, so it does not need the compatibility mouse events cancellation
    // suppresses. Refuse the focus-changing default before Android can lower the keyboard.
    // Capture-and-restore remains the backstop for a platform focus move that escapes that
    // refusal, with the dismissal count ensuring an intentional close still wins.
    const keyboard = { holder: softKeyboardHolder(), dismissals: softKeyboardDismissals() }
    const { roomAbove, scale } = fitAbove(event.clientY)
    const opened = controller.gesture.press(event.pointerId, event.clientX, event.clientY, {
      wedges,
      rings,
      slots: slotSpecs(),
      roomAbovePx: roomAbove,
      adoptStanding,
    })
    if (!opened) return
    keyboardRef.current = keyboard
    if (keyboard.holder) event.preventDefault()
    controller.begin(padHandlers())
    // A press that adopted the standing dial leaves the dial exactly where it is: its origin,
    // scale and visibility are the ones already on screen, and re-seeding them from this
    // press would move the fan out from under the finger aiming at it. The first reading is
    // driven here rather than from inside `press`, so the handlers above are bound before
    // anything can fire.
    if (adoptStanding) {
      controller.gesture.move(event.pointerId, event.clientX, event.clientY)
      return
    }
    // Client coordinates go in raw because the dial is portalled to `document.body`, which
    // is the one mount where they mean screen pixels. Rendering it inside the chip is not an
    // option: the rail's scroller sits between the pane's transform and the chip, and a
    // transformed ancestor makes that scroller clip even `position:fixed` descendants - the
    // dial would be cut off at the edge of the strip.
    //
    // `y` is the fan's *origin*, which sits `bands.lift` above the finger, not the press
    // itself. Read from the bands rather than from the constant so the drawing and the
    // gesture agree at every squeeze - a dial drawn around a different point than the one the
    // wedges are resolved against would be lying at exactly the moment it is being trusted.
    setDial({
      x: event.clientX,
      y: event.clientY - railPadBands(banded, scale).lift,
      scale,
      banded,
      latch: null,
      armed: false,
      beyond: false,
      visible: false,
      standing: false,
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
    if (rings !== 1) return null
    // Matched on the wedge's own derived name rather than on the wedge *count*, so an arrow
    // addresses the wedge that genuinely points that way at any count, and simply finds
    // nothing at a count with no such wedge - four wedges have a Left and a Right but no Up,
    // because their centres straddle the vertical. Still refusing rather than guessing; the
    // rule is just stated over the geometry instead of over a single supported shape.
    const wanted = key === 'ArrowLeft' ? 'Left' : key === 'ArrowUp' ? 'Up' : key === 'ArrowRight' ? 'Right' : null
    if (!wanted) return null
    for (let wedge = 0; wedge < wedges; wedge += 1) {
      if (padWedgeName(wedge, wedges) === wanted) return padSlotKey(0, wedge)
    }
    return null
  }

  // Drawn and read left to right, which is the reverse of the wedge index: wedge 0 is the
  // rightmost, because the fan's angles grow counter-clockwise from due east.
  const drawn = padSlotKeys(item.pad)
  const populated = drawn.filter(key => byKey.has(key))
  const accessible = [
    modifierPrefix ? `${modifierPrefix} pad` : 'Pad',
    ...populated.map(key => {
      const at = parsePadSlotKey(key)!
      return `${padWedgeName(at.wedge, wedges, at.ring)}: ${byKey.get(key)?.label}`
    }),
  ].join('. ')

  const bands = railPadBands(dial?.banded ?? banded, dial?.scale ?? 1)
  const innerOf = (ring: number) => ring > 0 ? bands.ring : bands.dead
  const outerOf = (ring: number) => ring > 0 || !Number.isFinite(bands.ring) ? bands.outer : bands.ring

  return <button
    ref={buttonRef}
    type="button"
    data-rail-item={item.id}
    data-rail-slot={slot}
    data-reorder-id={reorderId}
    class={`${className || 'term-key'} rail-pad rail-pad-w${wedges} rail-pad-r${rings}${dial ? ' rail-pad-pressed' : ''}`}
    title={item.title || 'Drag a wedge, or tap to open'}
    aria-label={accessible}
    aria-haspopup="menu"
    aria-expanded={!!dial?.standing}
    // Still here for the *tap*, which does deliver a `mousedown`. The drag is covered by the
    // capture-and-restore in `beginPress`, because a drag delivers none.
    onMouseDown={event => { event.preventDefault(); holdSoftKeyboard(event) }}
    onContextMenu={event => event.preventDefault()}
    onPointerDown={event => {
      if (!event.isPrimary || event.button !== 0) return
      beginPress(event as unknown as PointerEvent)
    }}
    onPointerUp={closeUnlessStanding}
    onPointerCancel={closeUnlessStanding}
    onLostPointerCapture={closeUnlessStanding}
    onKeyDown={event => {
      // Escape closes a standing dial before anything else looks at the key, the same
      // precedence every other rail overlay takes.
      if (event.key === 'Escape' && dial?.standing) { event.preventDefault(); dismiss(); return }
      const key = keySlot(event.key)
      if (!key || !byKey.has(key)) return
      event.preventDefault()
      // Running a slot ends the standing dial, exactly as a tapped wedge does: one
      // activation, one outcome, whichever route reached it.
      dismiss()
      runSlot(key)
    }}
    onClick={() => {
      // Enter and Space arrive here with no pointer behind them, which is the one route the
      // gesture never saw. They do what a tap does - open the dial - so the keyboard and the
      // finger agree about what a plain activation of this chip means.
      if (controller.gesture.consumeHandledClick()) return
      if (dial?.standing) { dismiss(); return }
      openFromKeyboard()
    }}
  >
    {content}
    {/* Populated wedges, marked inside the chip's own border. Drawn rather than laid out, so
        the chip is exactly the size it would be without them. One tick per wedge, pointing
        the way that wedge points; a far-ring one sits inboard of its near partner, which is
        the only thing on the chip that says a wedge has two depths.
        Placed against a *fixed inset from each edge* rather than at a percentage of the
        chip. A percentage put the horizontal wedges' ticks within a pixel of the border on a
        narrow chip, where the tick's arms lay along the border and read as part of it - the
        leftmost wedge of the four-arrow pad looked unbound when it was drawn all along. The
        inset is per-axis because a chip is much wider than it is tall. */}
    <span class="rail-pad-marks" aria-hidden="true">
      {populated.map(key => {
        const at = parsePadSlotKey(key)!
        const unit = padWedgeUnit(at.wedge, wedges)
        const reach = at.ring > 0 ? 0.7 : 1
        const along = (axis: 'x' | 'y', inset: number) =>
          `calc(50% + (50% - ${inset}px) * ${(unit[axis] * reach).toFixed(4)})`
        return <span
          key={key}
          class="rail-pad-mark"
          style={{
            left: along('x', 7),
            top: along('y', 5),
            // The glyph is a corner bracket whose apex points up-left, so `135 - centre`
            // aims it down its own wedge - *outward*, the way the drag goes. It used to
            // carry `-centre - 45`, which is 180° off: every tick pointed back at the
            // middle of the chip.
            transform: `translate(-50%,-50%) rotate(${135 - padWedgeCentreDeg(at.wedge, wedges)}deg)`,
          }}
        />
      })}
    </span>
    {dial && createPortal(<div
      class={`rail-pad-dial${dial.visible ? ' rail-pad-dial-shown' : ''}${dial.standing ? ' rail-pad-dial-standing' : ''}`}
      // A transient dial is a picture of a gesture in flight and says nothing worth hearing.
      // A standing one is a control, and is announced as the menu the chip's `aria-haspopup`
      // promised. It is deliberately not focus-managed: the number keys already run every
      // wedge from the chip, which keeps focus and therefore keeps Escape, so a roving
      // tabindex here would add a second navigation route to the same slots.
      aria-hidden={dial.standing ? undefined : 'true'}
      role={dial.standing ? 'menu' : undefined}
      aria-label={dial.standing ? accessible : undefined}
      // The whole surface, not the wedges. Hit-testing SVG paths would put a second copy of
      // the geometry in the drawing, and the point of `railPadResolve` is that there is only
      // one - so every press on the dial goes to the gesture as coordinates and the gesture
      // says what it hit, exactly as it does for a drag.
      onPointerDown={dial.standing
        ? (event: PointerEvent) => {
          if (!event.isPrimary || event.button !== 0) return
          event.preventDefault()
          beginPress(event, true)
        }
        : undefined}
      // The same focus refusal every rail chip makes, and it is load-bearing here for a
      // reason the chip's own guard cannot cover: the dial is already mounted and covering
      // the viewport by the time Chrome synthesises the opening tap's `mousedown`, so that
      // event lands on *this* element rather than the chip whose guard would have refused it.
      // Without it, tapping a pad on Android drops the soft keyboard.
      onMouseDown={dial.standing ? (event: Event) => event.preventDefault() : undefined}
      onContextMenu={dial.standing ? (event: Event) => event.preventDefault() : undefined}
    >
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
        {/* Where the finger actually is: low in the hub, because the fan is centred above
            it. Drawn because otherwise the hub reads as floating for no reason, and the one
            thing the operator has to understand about the lift is that they start inside the
            neutral disc and every wedge is up from there. */}
        <circle class="rail-pad-dial-grip" cy={bands.lift} r={3.5}/>
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
