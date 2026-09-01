/**
 * The beat's show layer: callouts, the radar sweep, the walk, the keycap HUD, shimmers,
 * arrival marks and the momentary scanlines.
 *
 * It is a second view beside `DemoDirector`, not a part of it, for the same reason the
 * director is beside `App`: this file is all measurement and animation, and the card is
 * all copy and controls. Keeping them apart means a change to how a label is drawn cannot
 * break the one control that stops a running scenario.
 *
 * Three things are load-bearing and easy to undo by accident:
 *
 * - **Everything is `pointer-events: none`.** The visitor's first real press is what hands
 *   the demo over, and a callout sits on top of the very control they are most likely to
 *   press; a chip that could take that click would swallow the handover. The CSS enforces
 *   it; nothing here may add a handler.
 * - **Placement is re-measured on DOM change, never on a timer.** The chrome a callout
 *   names moves when a panel opens or the sidebar scrolls, and a label pointing at where
 *   a row used to be is worse than no label.
 * - **Chips are keyed by the beat's sequence number.** Preact would otherwise reuse the
 *   element across beats, and a reused element does not replay its reveal animation - the
 *   overlay would look frozen from the second beat onwards.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import {
  anchorPoint, boxOf, gutterSide, placeCallouts, sweepAxis, sweepDelays, unionBox, wirePath,
  type Box, type Placed, type Show,
} from './callouts.ts'
import { firstVisible } from './drive.ts'

/** How long the radar band takes to cross its column. */
const SWEEP_MS = 1_500
/**
 * How long the walk holds on each target before moving to the next.
 *
 * Long enough to read a two-word label *and* look at what it points at, which is the whole
 * job: six labels on screen together are six things being pointed at, and a visitor
 * reading any one of them has to work out which line belongs to it first. The number has
 * been raised twice from a first guess of a second, both times because the eye has to
 * travel from the label to a target somewhere else on the screen and back.
 */
const WALK_MS = 1_800
/** The gap between one chip's reveal and the next, when they all arrive together. */
const STAGGER_MS = 95
/** How thick the radar band is across its travel, and so how far off-column it starts.
 *  Shared with the stylesheet, which sizes the band on the other axis. */
const BAND_DEPTH = 74

/**
 * Measure one callout's target, or nothing when this beat's chrome is not on screen.
 *
 * "On screen" includes the viewport, not just the DOM. The command rail scrolls
 * horizontally on a phone, so a chip can be rendered, visible to `firstVisible`, and sit
 * two hundred pixels past the right edge - and a label for it gets clamped back into the
 * frame with a leader line running out towards nothing. A box *wholly* outside the
 * viewport can never be usefully labelled, so it is treated as absent; a box that merely
 * overhangs an edge is still measured, because half a session row is still that row.
 */
const measure = (selectors: string[]): Box | null => {
  const element = firstVisible(selectors)
  if (!element) return null
  const box = boxOf(element.getBoundingClientRect())
  const off = box.right <= 0 || box.left >= innerWidth
    || box.bottom <= 0 || box.top >= innerHeight
  return off ? null : box
}

const measureAll = (groups: string[][]): Box[] =>
  groups.map(measure).filter((box): box is Box => box !== null)

export function DemoShow({ show, seq }: { show: Show; seq: number }) {
  // Memoised because it is an effect dependency and `?? []` would otherwise mint a new
  // array on every render, re-creating the MutationObserver for nothing.
  const notes = useMemo(() => show.notes ?? [], [show])
  const mode = show.reveal ?? 'glitch'
  /** How long this walk rests on each stop; see `Show.hold`. */
  const hold = show.hold ?? WALK_MS
  const chips = useRef<Array<HTMLDivElement | null>>([])
  const [placed, setPlaced] = useState<Placed[]>([])
  const [column, setColumn] = useState<Box | null>(null)
  const [extras, setExtras] = useState<{ shimmer: Box[]; arrive: Box[] }>(
    { shimmer: [], arrive: [] },
  )
  const [step, setStep] = useState(0)
  /** How many of this beat's notes last measured, which is what the walk cycles over. */
  const [live, setLive] = useState(0)
  /** Whether anything this beat names has been on screen yet. */
  const [armed, setArmed] = useState(false)

  /**
   * Measure, place, and keep doing both while the beat is on screen.
   *
   * The chips are rendered before they are placed, at `visibility: hidden`, because their
   * widths are what the placement needs and only the browser knows them. That costs one
   * frame nobody can see and removes the alternative, which is estimating a monospace
   * width from the label's length and being wrong on every theme change.
   */
  useLayoutEffect(() => {
    let frame = 0
    const run = (): void => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const viewport = { width: innerWidth, height: innerHeight }
        const entries = notes.map((callout, index) => {
          const target = measure(callout.at)
          const chip = chips.current[index]
          if (!target || !chip) return null
          return { callout, target, width: chip.offsetWidth, height: chip.offsetHeight }
        }).filter((entry): entry is NonNullable<typeof entry> => entry !== null)
        // A walk shows one label at a time, so it places one: laying the whole set out
        // and then hiding all but one puts the gutter wherever the *widest spread* of
        // targets wants it, which drew a label for a sidebar row against the far right
        // edge with a leader line the width of the screen.
        //
        // Indexed into the *measured* entries rather than into the beat's notes, so a note
        // whose chrome is not on screen costs no stop at all. Walking the written list
        // instead spends 1.8s on a blank frame, which reads as the tour having lost its
        // place - and the phone's rail scrolls, so it is not a hypothetical.
        setLive(current => (current === entries.length ? current : entries.length))
        const active = mode === 'walk' && entries.length
          ? [entries[step % entries.length]]
          : entries
        // The side, though, is decided from the whole set even when one label is placed.
        // A row of rail chips is a row whichever of them is being named, and asking the
        // active target alone would let the gutter jump from above the strip to beside
        // one chip between two stops of the same walk.
        const side = gutterSide(entries.map(item => item.target), viewport)
        // A beat publishes before its own press runs, so the chrome it labels routinely
        // arrives a second later. The walk's clock waits for that rather than starting on
        // the beat: it used to spend its first stop pointing at a dialog that had not
        // opened yet, and the visitor saw the tour skip a label it never drew.
        if (entries.length) setArmed(true)
        const next = placeCallouts(active, viewport, side)
        setPlaced(current => (samePlacement(current, next) ? current : next))
        const band = show.sweep
          ? measure(show.sweep)
          : unionBox(entries.map(entry => entry.target))
        setColumn(current => (sameBox(current, band) ? current : band))
        setExtras(current => {
          const shimmer = measureAll(show.shimmer ?? [])
          const arrive = measureAll(show.arrive ?? [])
          return sameBoxes(current.shimmer, shimmer) && sameBoxes(current.arrive, arrive)
            ? current
            : { shimmer, arrive }
        })
      })
    }
    run()
    const observer = new MutationObserver(run)
    observer.observe(document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'],
    })
    addEventListener('resize', run)
    addEventListener('scroll', run, true)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      removeEventListener('resize', run)
      removeEventListener('scroll', run, true)
    }
    // `seq` is in the list so a new beat re-measures from scratch rather than inheriting
    // the previous one's placement for a frame, and `step` because a walk places only
    // the label it is currently holding on.
  }, [seq, show, mode, step, notes])

  /**
   * The walk's own clock. One target at a time, in the order the beat wrote them.
   *
   * Three things it waits for or does, each of which was a way of being unreadable:
   *
   * - It does not start until something it can point at is on screen.
   * - It lets the radar band finish crossing first, so the scan reads as one gesture
   *   rather than as a band racing a label. A beat with no band waits `hold` instead,
   *   which is what gives chrome the beat just opened a moment to be seen.
   * - It **stops on its last label** rather than wrapping. It used to wrap, from when the
   *   walkthrough's beats were gates and a beat lasted until the visitor acted; beats are
   *   timed now, and every one of them is given enough clock for a full pass
   *   (`demoDirector.test.ts`), so wrapping only ever meant starting the list again in
   *   the seconds before the next beat - which reads as the tour having lost its place.
   *
   * It cycles over `live` rather than over `notes`: a note whose chrome is off screen
   * costs no stop at all, where walking the written list would spend a stop on a blank
   * frame.
   */
  useEffect(() => {
    if (mode !== 'walk' || live < 2 || !armed) return
    let tick = 0
    const lead = setTimeout(() => {
      // The updater clamps rather than the interval clearing itself: a state updater has
      // to be pure, and a timer that keeps firing on an unchanged value costs nothing and
      // is cleared with the beat.
      tick = setInterval(
        () => setStep(current => Math.min(current + 1, live - 1)),
        hold,
      ) as unknown as number
    }, show.sweep ? SWEEP_MS : (show.hold ?? 0))
    return () => { clearTimeout(lead); clearInterval(tick) }
  }, [seq, mode, live, armed, show.sweep, show.hold, hold])

  /** A new beat starts its walk from the top, and disarms until it has measured. */
  useEffect(() => {
    setStep(0)
    setLive(0)
    setArmed(false)
  }, [seq])

  /** Whether the radar band has finished crossing. A walk holds its first label until it
   *  has: the scan says "all of this", and answering it before it has finished asking
   *  puts two things on screen that are each other's noise. */
  const [swept, setSwept] = useState(false)
  useEffect(() => {
    setSwept(!show.sweep)
    if (!show.sweep) return
    const done = setTimeout(() => setSwept(true), SWEEP_MS)
    return () => clearTimeout(done)
  }, [seq, show.sweep])

  // `placed` is already only what this beat is showing - a walk places one label at a
  // time - so there is no second filter here beyond the sweep's hold, and the delays line
  // up with it by index.
  // The hold is the walk's alone: `sweep` mode wakes its labels *with* the band on
  // purpose, and holding them there would delete the effect rather than tidy it.
  const visible = mode === 'walk' && !swept ? [] : placed
  const visibleDelays = mode === 'sweep' && column
    ? sweepDelays(placed.map(item => item.target), column, SWEEP_MS)
    : placed.map((_, index) => (mode === 'walk' ? 0 : index * STAGGER_MS))
  const spot = mode === 'walk' && swept ? placed[0]?.target ?? null : null

  return <div class={`demo-show mode-${mode}`}>
    {show.crt && <div class="demo-show-crt" aria-hidden="true" />}

    {/* The walk dims the frame and cuts one hole in it. A ring would have to compete with
        everything else on screen; a hole has nothing to compete with. */}
    {spot && <div class="demo-show-cutout" style={{
      left: spot.left - 6, top: spot.top - 5, width: spot.width + 12, height: spot.height + 10,
    }} aria-hidden="true" />}

    {/* The band is the scan, and it is not tied to the reveal mode: a beat can sweep the
        column once to say "all of this", then walk it one label at a time to say what each
        part is. Only an explicit `sweep` target draws it - in the other modes `column` is
        the union of the targets, which is a box rather than a thing to scan. */}
    {show.sweep && column && <SweepBand key={`sweep-${seq}`} column={column} />}

    <svg class="demo-show-wires" aria-hidden="true">
      {visible.map((item, index) => <g
        key={`wire-${seq}-${item.callout.label}`}
        style={{ animationDelay: `${visibleDelays[index] ?? 0}ms` }}
      >
        {/* `pathLength` normalises the dash to the line's own length, so one CSS rule
            draws a 40px elbow and a 400px one at the same speed. */}
        <path d={wirePath(item)} pathLength={1} />
        <circle cx={anchorPoint(item).x} cy={anchorPoint(item).y} r="2.4" />
      </g>)}
    </svg>

    {visible.map((item, index) => <i
      key={`mark-${seq}-${item.callout.label}`}
      class={`demo-show-mark ${mode === 'blueprint' ? 'bracket' : ''}`}
      style={{
        left: item.target.left - 4, top: item.target.top - 3,
        width: item.target.width + 8, height: item.target.height + 6,
        animationDelay: `${visibleDelays[index] ?? 0}ms`,
      }}
      aria-hidden="true"
    />)}

    {/* Rendered for every note, placed for the ones that measured. The hidden pass is
        what gives the placement its widths, so the list cannot be filtered first. */}
    {notes.map((callout, index) => {
      const order = visible.findIndex(entry => entry.callout === callout)
      const item = order >= 0 ? visible[order] : null
      return <div
        key={`chip-${seq}-${index}`}
        ref={element => { chips.current[index] = element }}
        class={`demo-show-chip ${item ? '' : 'measuring'}`}
        style={item
          ? {
            // The placement hands back the chip's own box for every side, so one rule
            // draws all four. It used to anchor the right edge for a left-hand gutter,
            // which was the same arithmetic done twice - once here and once in the
            // module that already knew the width.
            left: item.left,
            top: item.top,
            animationDelay: `${visibleDelays[order] ?? 0}ms`,
          }
          : { left: 0, top: 0 }}
      >
        {callout.label}
        {callout.sub && <span class="demo-show-sub">{callout.sub}</span>}
      </div>
    })}

    {extras.arrive.map((box, index) => <i
      key={`arrive-${seq}-${index}`}
      class="demo-show-arrive"
      style={{
        left: box.left - 3, top: box.top - 2, width: box.width + 6, height: box.height + 4,
        animationDelay: `${index * 170}ms`,
      }}
      aria-hidden="true"
    />)}

    {extras.shimmer.map((box, index) => <i
      key={`shimmer-${seq}-${index}`}
      class="demo-show-shimmer"
      style={{
        left: box.left - 3, top: box.top - 2, width: box.width + 6, height: box.height + 4,
        animationDelay: `${index * 240}ms`,
      }}
      aria-hidden="true"
    />)}

    {show.keys && show.keys.length > 0 && <div class="demo-show-keys" aria-hidden="true">
      {show.keys.map((cap, index) => <i
        key={`cap-${seq}-${index}`}
        class={cap === '→' ? 'then' : 'hit'}
        style={{ animationDelay: `${index * 190}ms` }}
      >{cap}</i>)}
    </div>}
  </div>
}

/**
 * The radar band, as its own element because its travel is a measured distance.
 *
 * The distance goes in through `setProperty` rather than through the style object: a CSS
 * custom property in JSX is not part of the typed style surface, and the alternative -
 * writing the whole animation inline - would put the easing and the shape of the effect
 * in a TypeScript file instead of beside the rest of the demo's CSS.
 *
 * It crosses the long way, which for the fleet column is downwards and for the command
 * rail is sideways. A band that always travelled down would cross the rail's thirty
 * pixels in one frame and read as a flash.
 */
function SweepBand({ column }: { column: Box }) {
  const band = useRef<HTMLDivElement>(null)
  const across = sweepAxis(column) === 'across'
  const travel = Math.round((across ? column.width : column.height) + BAND_DEPTH)
  useLayoutEffect(() => {
    band.current?.style.setProperty('--sweep-travel', `${travel}px`)
    band.current?.style.setProperty('--sweep-ms', `${SWEEP_MS}ms`)
  }, [travel])
  return <div
    ref={band}
    class={`demo-show-sweep ${across ? 'across' : 'down'}`}
    style={across
      ? { top: column.top, height: column.height, left: column.left - BAND_DEPTH }
      : { left: column.left, width: column.width, top: column.top - BAND_DEPTH }}
    aria-hidden="true"
  />
}

// ------------------------------------------------------------------ change detection

const near = (left: number, right: number): boolean => Math.abs(left - right) < 0.5

const sameBox = (left: Box | null, right: Box | null): boolean =>
  left === right || Boolean(left && right && near(left.left, right.left)
    && near(left.top, right.top) && near(left.width, right.width)
    && near(left.height, right.height))

const sameBoxes = (left: Box[], right: Box[]): boolean =>
  left.length === right.length && left.every((box, index) => sameBox(box, right[index]))

/**
 * Whether a fresh placement is the one already on screen.
 *
 * Without this the MutationObserver's own re-render feeds the observer again and the
 * overlay repaints every frame - which is not only wasteful, it restarts each chip's
 * reveal animation and makes the whole layer flicker.
 */
const samePlacement = (left: Placed[], right: Placed[]): boolean =>
  left.length === right.length && left.every((item, index) =>
    item.callout === right[index].callout
    && near(item.x, right[index].x) && near(item.y, right[index].y)
    && sameBox(item.target, right[index].target))
