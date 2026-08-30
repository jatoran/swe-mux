// The session state indicator: one shape, drawn as SVG so the shape, the hollow
// "standing" variant, and the context gauge that wraps it are all the same
// primitive.
//
// Why SVG and not CSS: a hexagon is `clip-path`, a hollow hexagon is not (a
// clipped border follows the box, not the polygon), and a gauge that follows a
// hexagon's outline is nothing CSS can express at all. In SVG all three fall out
// of one path: `pathLength="100"` normalizes every shape's perimeter to the same
// scale, so `stroke-dashoffset` fills a circle, a square, and a hexagon by the
// same arithmetic, clockwise from twelve o'clock.
//
// Colour still comes from CSS (`--dot`), so themes keep overriding one variable
// rather than a set of fills.

import { hasRunningActivity, sessionDotClass } from './sessionStatus.ts'
import { shapePath } from './dotShapes.ts'
import type { ContextBand, DotShape } from './sessionRowConfig.ts'
import type { Session } from './types'

/** Outer radius of the gauge ring, sized so ring plus stroke still fits the viewBox. */
const RING_RADIUS = 10.2
/** Radius of the filled core, in a 24-unit box. */
const CORE_RADIUS = 6.2
/** Stroke width of the hollow "standing" core. */
const HOLLOW_STROKE = 2.4

export interface StateIndicatorProps {
  session: Session | undefined
  shape: DotShape
  /**
   * Context pressure drawn as an outline around the shape. Omit to draw none.
   *
   * `band` arrives pre-computed rather than derived here from a threshold prop:
   * the ramp is configurable, and a second comparison chain in the one component
   * every surface draws is how the arc and the row's gauge come to disagree at
   * the boundary the user just moved.
   */
  gauge?: { pct: number; peak: number; band: ContextBand } | null
  /**
   * Standing activity collapsed to a corner pip, with the label it carries.
   * Supplied by `sessionStandingMark`, so it is present only in the rendering
   * that asks for it and the row never draws the same fact twice.
   */
  standing?: { label: string } | null
  /** Extra classes for surfaces that position the indicator themselves. */
  class?: string
}

/** The ramp's class suffix. `calm` is the base rule and adds nothing. */
const bandClass = (band: ContextBand): string => (band === 'calm' ? '' : ` ${band}`)

/**
 * The indicator. `sessionDotClass` still supplies the state classes, so every
 * existing colour rule, pulse, and reduced-motion override keeps applying and
 * the hollow `standing` variant keeps meaning "engaged, but you can type".
 */
export function StateIndicator({ session, shape, gauge, standing, class: extra }: StateIndicatorProps) {
  const hollow = Boolean(session && session.state === 'idle' && hasRunningActivity(session))
  const outline = shapePath(shape, RING_RADIUS)
  const pct = gauge ? Math.max(0, Math.min(1, gauge.pct)) : 0
  const peak = gauge ? Math.max(0, Math.min(1, gauge.peak)) : 0
  return <span class={`${sessionDotClass(session)} state-indicator shape-${shape}${extra ? ` ${extra}` : ''}`} title={standing?.label}>
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {gauge && <path class="ind-track" d={outline} />}
      {gauge && <path class={`ind-fill${bandClass(gauge.band)}`} d={outline} pathLength={100} stroke-dasharray="100" stroke-dashoffset={100 - pct * 100} />}
      {/* The peak sits on the same normalized outline as a 1.5-unit dash, so it
          follows whichever shape is selected without any per-shape geometry. */}
      {gauge && peak > pct && <path class="ind-peak" d={outline} pathLength={100} stroke-dasharray="1.5 98.5" stroke-dashoffset={-peak * 100} />}
      {/* The hollow path is inset by half its stroke so both variants occupy the
          same outer silhouette; drawn at the same radius, the stroked one would
          read as the larger shape and "engaged" would look like a bigger state. */}
      <path
        class={hollow ? 'ind-core hollow' : 'ind-core'}
        d={shapePath(shape, hollow ? CORE_RADIUS - HOLLOW_STROKE / 2 : CORE_RADIUS)}
      />
    </svg>
    {/* Deliberately a CSS pip outside the SVG rather than another path inside
        it. A 24-unit box with a 6.2 core and a 10.2 ring has no empty annulus
        left: a mark placed on the ring is indistinguishable from the context
        gauge's peak dash, and one placed inside it lands on the state colour.
        The box's top-right *corner* is empty for every shape, and a pip sized in
        pixels also stays legible at a 10px indicator, where a shape-relative one
        would be under two pixels across. */}
    {standing && <i class="ind-standing" aria-hidden="true" />}
  </span>
}
