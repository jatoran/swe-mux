// Geometry for the session state indicator.
//
// One normalized outline per shape, always starting at twelve o'clock and
// running clockwise, so a partially stroked path reads as a clock face whichever
// shape is selected. Paired with SVG's `pathLength`, this is what lets a circle,
// a square, and a hexagon share one gauge implementation: the perimeter is
// rescaled to 100 units regardless of the real path length, so `dashoffset`
// arithmetic is identical for all three.

import type { DotShape } from './sessionRowConfig.ts'

/** Shared centre of the 24×24 indicator viewBox. */
export const INDICATOR_CENTER = 12

function polygonPath(sides: number, radius: number): string {
  const points: string[] = []
  for (let index = 0; index < sides; index += 1) {
    const angle = (-90 + (360 / sides) * index) * (Math.PI / 180)
    const x = INDICATOR_CENTER + radius * Math.cos(angle)
    const y = INDICATOR_CENTER + radius * Math.sin(angle)
    points.push(`${index === 0 ? 'M' : 'L'}${round(x)},${round(y)}`)
  }
  return `${points.join('')}Z`
}

/** Trim float noise so identical shapes produce identical strings (and stable vnodes). */
const round = (value: number): string => Number(value.toFixed(3)).toString()

function circlePath(radius: number): string {
  const top = round(INDICATOR_CENTER - radius)
  const bottom = round(INDICATOR_CENTER + radius)
  const r = round(radius)
  return `M${INDICATOR_CENTER},${top}A${r},${r} 0 0 1 ${INDICATOR_CENTER},${bottom}A${r},${r} 0 0 1 ${INDICATOR_CENTER},${top}Z`
}

/**
 * Axis-aligned square, traversed from the *midpoint of the top edge* clockwise.
 *
 * A regular 4-gon generated from vertices would start at a corner and render as
 * a diamond; starting at the top edge's midpoint is what keeps a partial gauge
 * fill reading as a clock across all three shapes.
 */
function squarePath(radius: number): string {
  // Visual weight, not geometry: a square of half-side `radius` covers far more
  // ink than a circle of the same radius and reads as the larger shape.
  const half = radius * SQUARE_RATIO
  const near = round(INDICATOR_CENTER - half)
  const far = round(INDICATOR_CENTER + half)
  return `M${INDICATOR_CENTER},${near}L${far},${near}L${far},${far}L${near},${far}L${near},${near}Z`
}

const SQUARE_RATIO = 0.86

/** Outline of `shape` at `radius`, as an SVG path. */
export function shapePath(shape: DotShape, radius: number): string {
  if (shape === 'circle') return circlePath(radius)
  if (shape === 'square') return squarePath(radius)
  return polygonPath(6, radius)
}
