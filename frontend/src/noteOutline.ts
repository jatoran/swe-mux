// The heading outline of a note.
//
// Derived from the snapshot text here rather than from Continuity's `presentation().blocks`,
// for three reasons. The block `kind` strings for headings are not part of the documented SDK
// surface: they come out of WASM and can move under a version bump, which is the same coupling
// `noteFind.ts` avoids. A `Span` carries whole-document byte offsets while `revealRange` wants
// `{line, byteInLine}`, and a plain `Presentation` has no line index to convert with (only
// `PresentationRange` carries `lineStarts`), so the line table would have to be rebuilt from
// the text regardless. And `presentation()` materializes every line of the document to hand
// back a handful of headings, on exactly the long notes an outline exists for.
//
// Pure, and with no runtime import from the editor package, so the node type-stripping test
// runner can load it. Positions are the `{line, byteInLine}` UTF-8 byte offsets Continuity's
// `Range` wants.

// Extension-bearing, like the other src modules the node test runner loads directly.
import { byteForUtf16Index, comparePositions, type SelectionPosition } from './noteSelection.ts'

export type OutlineHeading = {
  /** ATX depth, 1 to 6. */
  level: number
  /** The heading's visible text: `#` markers and any closing sequence removed. */
  text: string
  /** 0-based source line. */
  line: number
  /** The whole heading line. Collapse to `start` to place a caret, pass both to `revealRange`. */
  start: SelectionPosition
  end: SelectionPosition
}

type Fence = { char: string; length: number }

// Up to three leading spaces, per CommonMark. A tab-indented line is an indented code block
// (a tab counts as four columns), and `^ {0,3}` rejects it without a special case.
const FENCE_LINE = /^ {0,3}(`{3,}|~{3,})(.*)$/
// The hashes must be followed by whitespace or end the line: `#foo` is not a heading, which is
// what keeps a bare `#tag` out of the outline.
const ATX_LINE = /^ {0,3}(#{1,6})(?:[ \t]+(.*))?$/

/** A fence opener, or null. A backtick opener's info string may not contain a backtick. */
function fenceOpener(source: string): Fence | null {
  const match = FENCE_LINE.exec(source)
  if (!match) return null
  const char = match[1][0]
  if (char === '`' && match[2].includes('`')) return null
  return { char, length: match[1].length }
}

/** Does this line close `fence`? Same character, at least as long, nothing but space after. */
function closesFence(source: string, fence: Fence): boolean {
  const match = FENCE_LINE.exec(source)
  if (!match) return false
  return match[1][0] === fence.char && match[1].length >= fence.length && match[2].trim() === ''
}

/**
 * The visible text of an ATX heading.
 *
 * A trailing run of `#` preceded by whitespace is a closing sequence and is dropped, but only
 * when nothing follows it: `## foo #bar` keeps its `#bar`, because that run does not end the
 * line. A remainder that is nothing but hashes (`# #`) is an empty heading.
 */
function headingText(rest: string | undefined): string {
  const body = (rest ?? '').replace(/[ \t]+$/, '')
  if (/^#+$/.test(body)) return ''
  return body.replace(/[ \t]+#+$/, '').trim()
}

/**
 * Every ATX heading in the note, in document order.
 *
 * Fenced code is tracked and skipped: a notes file is full of prose about code, and a `# foo`
 * inside a fence is a comment rather than a landmark. Setext headings (`===` / `---` under a
 * line) are deliberately not recognised, because the `---` form is ambiguous with a thematic
 * break and guessing wrong puts a phantom entry in the list.
 */
export function outlineHeadings(text: string): OutlineHeading[] {
  const lines = text.split('\n')
  const headings: OutlineHeading[] = []
  let fence: Fence | null = null
  for (let line = 0; line < lines.length; line++) {
    const source = lines[line]
    if (fence) {
      if (closesFence(source, fence)) fence = null
      continue
    }
    const opener = fenceOpener(source)
    if (opener) {
      fence = opener
      continue
    }
    const match = ATX_LINE.exec(source)
    if (!match) continue
    headings.push({
      level: match[1].length,
      text: headingText(match[2]),
      line,
      start: { line, byteInLine: 0 },
      end: { line, byteInLine: byteForUtf16Index(source, source.length) },
    })
  }
  return headings
}

/**
 * Which heading the caret sits under: the last one at or above it, or -1 when the caret is
 * above the first heading. The outline opens focused on this row, so it answers "where am I"
 * the way `matchIndexAfter` does for the find bar.
 */
export function headingIndexAt(
  headings: readonly OutlineHeading[],
  caret: SelectionPosition | null,
): number {
  if (!caret) return -1
  let found = -1
  for (let index = 0; index < headings.length; index++) {
    if (comparePositions(headings[index].start, caret) > 0) break
    found = index
  }
  return found
}

/**
 * The ancestor chain ending at `index`, outermost first: what a breadcrumb over the reading
 * position shows.
 *
 * Walking back and keeping only strictly shallower headings is what makes a sibling replace
 * rather than append. Two headings of the same level are alternatives, not a nesting, so a
 * second `#` truncates the trail to itself instead of extending it.
 *
 * Levels are used as written rather than through the depth ladder: the ladder exists to keep
 * the outline from indenting a whole note that starts at `##`, whereas a trail has to respect
 * the document's real nesting or it would claim a `###` sits inside an unrelated `##`.
 */
export function headingTrail(
  headings: readonly OutlineHeading[],
  index: number,
): OutlineHeading[] {
  if (index < 0 || index >= headings.length) return []
  const trail = [headings[index]]
  let level = headings[index].level
  for (let cursor = index - 1; cursor >= 0 && level > 1; cursor--) {
    if (headings[cursor].level >= level) continue
    trail.unshift(headings[cursor])
    level = headings[cursor].level
  }
  return trail
}

/**
 * How far to indent each row, relative to the shallowest heading present.
 *
 * Depth counts distinct levels rather than hashes: a note whose top level is `##` should not
 * render every row indented, and a jump from `#` straight to `###` should cost one step rather
 * than two. Notes are written loosely enough that the raw hash count is a poor guide.
 */
export function outlineDepths(headings: readonly OutlineHeading[]): number[] {
  const ladder = [...new Set(headings.map(heading => heading.level))].sort((a, b) => a - b)
  return headings.map(heading => ladder.indexOf(heading.level))
}
