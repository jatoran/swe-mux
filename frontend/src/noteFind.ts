// Finding text inside a note.
//
// Continuity has no find of its own, and the browser's cannot stand in: the projection
// realizes only the lines in the viewport, so an offscreen match is not in the DOM to be
// found. Matching is therefore ours — scan the snapshot text here, and hand the engine the
// ranges to paint through `setDecorations` (0.2.17).
//
// Pure, and with no runtime import from the editor package, so the node type-stripping test
// runner can load it. Positions are `{line, byteInLine}` UTF-8 byte offsets, which is the
// shape Continuity's `Range` wants.

// Extension-bearing, like the other src modules the node test runner loads directly.
import { byteForUtf16Index, comparePositions, type SelectionPosition } from './noteSelection.ts'

export type FindRange = { start: SelectionPosition; end: SelectionPosition }
export type FindOptions = { matchCase?: boolean }

/**
 * Every non-overlapping occurrence of `query`, in document order.
 *
 * Matching is plain substring, never a regular expression: the query comes straight from a
 * text field over prose, so an unescaped `(` or `*` would either throw or quietly match
 * something the user did not ask for.
 *
 * A query spanning a line break never matches. Continuity ranges are line-scoped, and the
 * find bar is a single-line field, so there is nothing to gain by stitching lines together.
 */
export function findMatches(
  text: string,
  query: string,
  options: FindOptions = {},
): FindRange[] {
  if (!query) return []
  const matchCase = options.matchCase === true
  const needle = matchCase ? query : query.toLowerCase()
  const ranges: FindRange[] = []
  const lines = text.split('\n')
  for (let line = 0; line < lines.length; line++) {
    const source = lines[line]
    const folded = matchCase ? source : source.toLowerCase()
    // Case folding is not always length-preserving (`İ` lowercases to two units), and a
    // line where it is not would slide every offset after it. Those lines match
    // case-sensitively instead of reporting a range that points at the wrong bytes.
    const usable = matchCase || folded.length === source.length
    const haystack = usable ? folded : source
    const target = usable ? needle : query
    for (let from = 0; from <= haystack.length - target.length; ) {
      const at = haystack.indexOf(target, from)
      if (at < 0) break
      const end = at + target.length
      ranges.push({
        start: { line, byteInLine: byteForUtf16Index(source, at) },
        end: { line, byteInLine: byteForUtf16Index(source, end) },
      })
      from = end
    }
  }
  return ranges
}

/**
 * Which match to show first: the one at or after `caret`, so opening the bar continues from
 * where the user was reading rather than jumping to the top of the note. Wraps to the first
 * match when the caret sits past the last one, and answers 0 for an empty set so callers can
 * index without a special case.
 */
export function matchIndexAfter(
  ranges: readonly FindRange[],
  caret: SelectionPosition | null,
): number {
  if (!caret) return 0
  const found = ranges.findIndex(range => comparePositions(range.start, caret) >= 0)
  return found < 0 ? 0 : found
}

/** Step through matches, wrapping at both ends. Answers 0 for an empty set. */
export function stepMatchIndex(count: number, index: number, backwards: boolean): number {
  if (count <= 0) return 0
  return (index + (backwards ? -1 : 1) + count) % count
}
