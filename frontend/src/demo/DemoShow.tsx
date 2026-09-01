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
 * - **Everything is `pointer-events: none`.** A gated walkthrough beat is waiting for the
 *   visitor to press the very control a callout is labelling, so a chip that could take
 *   the click would make the tour impossible to finish. The CSS enforces it; nothing here
 *   may add a handler.
 * - **Placement is re-measured on DOM change, never on a timer.** The chrome a callout
 *   names moves when a panel opens or the sidebar scrolls, and a label pointing at where
 *   a row used to be is worse than no label.
 * - **Chips are keyed by the beat's sequence number.** Preact would otherwise reuse the
 *   element across beats, and a reused element does not replay its reveal animation - the
 *   overlay would look frozen from the second beat onwards.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import {
  anchorPoint, boxOf, placeCallouts, sweepDelays, unionBox, wirePath,
  type Box, type Placed, type Show,
} from './callouts.ts'
import { firstVisible } from './drive.ts'

/** How long the radar band takes to cross its column. */
const SWEEP_MS = 1_500
/** How long the walk holds on each target before moving to the next. */
const WALK_MS = 1_050
/** The gap between one chip's reveal and the next, when they all arrive together. */
const STAGGER_MS = 95

/** Measure one callout's target, or nothing when this beat's chrome is not on screen. */
const measure = (selectors: string[]): Box | null => {
  const element = firstVisible(selectors)
  return element ? boxOf(element.getBoundingClientRect()) : null
}

const measureAll = (groups: string[][]): Box[] =>
  groups.map(measure).filter((box): box is Box => box !== null)

export function DemoShow({ show, seq }: { show: Show; seq: number }) {
  // Memoised because it is an effect dependency and `?? []` would otherwise mint a new
  // array on every render, re-creating the MutationObserver for nothing.
  const notes = useMemo(() => show.notes ?? [], [show])
  const mode = show.reveal ?? 'glitch'
  const chips = useRef<Array<HTMLDivElement | null>>([])
  const [placed, setPlaced] = useState<Placed[]>([])
  const [column, setColumn] = useState<Box | null>(null)
  const [extras, setExtras] = useState<{ shimmer: Box[]; arrive: Box[] }>(
    { shimmer: [], arrive: [] },
  )
  const [step, setStep] = useState(0)
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
        const active = mode === 'walk'
          ? entries.filter(item => item.callout === notes[step])
          : entries
        // A beat publishes before its own press runs, so the chrome it labels routinely
        // arrives a second later. The walk's clock waits for that rather than starting on
        // the beat: it used to spend its first stop pointing at a dialog that had not
        // opened yet, and the visitor saw the tour skip a label it never drew.
        if (entries.length) setArmed(true)
        const next = placeCallouts(active, viewport)
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

  /** The walk's own clock. One target at a time, in the order the beat wrote them, and
   *  not started until something it can point at is actually on screen. */
  useEffect(() => {
    if (mode !== 'walk' || notes.length < 2 || !armed) return
    const tick = setInterval(
      () => setStep(current => (current + 1 >= notes.length ? current : current + 1)),
      WALK_MS,
    )
    return () => clearInterval(tick)
  }, [seq, mode, notes.length, armed])

  /** A new beat starts its walk from the top, and disarms until it has measured. */
  useEffect(() => {
    setStep(0)
    setArmed(false)
  }, [seq])

  // `placed` is already only what this beat is showing - a walk places one label at a
  // time - so there is no second filter here, and the delays line up with it by index.
  const visible = placed
  const visibleDelays = mode === 'sweep' && column
    ? sweepDelays(placed.map(item => item.target), column, SWEEP_MS)
    : placed.map((_, index) => (mode === 'walk' ? 0 : index * STAGGER_MS))
  const spot = mode === 'walk' ? placed[0]?.target ?? null : null

  return <div class={`demo-show mode-${mode}`}>
    {show.crt && <div class="demo-show-crt" aria-hidden="true" />}

    {/* The walk dims the frame and cuts one hole in it. A ring would have to compete with
        everything else on screen; a hole has nothing to compete with. */}
    {spot && <div class="demo-show-cutout" style={{
      left: spot.left - 6, top: spot.top - 5, width: spot.width + 12, height: spot.height + 10,
    }} aria-hidden="true" />}

    {mode === 'sweep' && column && <SweepBand key={`sweep-${seq}`} column={column} />}

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
      const order = placed.findIndex(entry => entry.callout === callout)
      const item = order >= 0 ? placed[order] : null
      return <div
        key={`chip-${seq}-${index}`}
        ref={element => { chips.current[index] = element }}
        class={`demo-show-chip ${item ? '' : 'measuring'}`}
        style={item
          ? {
            left: item.side === 'right' ? item.x : undefined,
            right: item.side === 'left' ? innerWidth - item.x : undefined,
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
 */
function SweepBand({ column }: { column: Box }) {
  const band = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    band.current?.style.setProperty('--sweep-travel', `${Math.round(column.height + 74)}px`)
    band.current?.style.setProperty('--sweep-ms', `${SWEEP_MS}ms`)
  }, [column.height])
  return <div
    ref={band}
    class="demo-show-sweep"
    style={{ left: column.left, width: column.width, top: column.top - 74 }}
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
