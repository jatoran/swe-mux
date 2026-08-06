import assert from 'node:assert/strict'
import test from 'node:test'
import {
  HEADING_JUMP,
  headingJumpDelta,
  measuredLineHeight,
  scrollLineIntoView,
  seedLineHeight,
  type LineWindow,
  type ViewportScroller,
} from '../src/noteScroll.ts'

/**
 * A document the host cannot model: rows of unequal height, and a scroller whose pixels move
 * the rendered content by more than they move the scroll offset.
 *
 * Both are the real editor. A projected heading renders at up to 1.45em, and whatever surplus
 * height the textarea's bottom padding cannot absorb is applied to the projection as a
 * proportional ramp, so a pixel of scroll is worth slightly more than a pixel of content. A jump
 * computed from any fixed row height lands short of the target by exactly this, which is the
 * bug the feedback loop exists to close.
 */
function simulatedNote(options: {
  lines: number
  viewport: number
  /** Every nth line is a heading, rendered taller. */
  headingEvery: number
  bodyHeight: number
  headingHeight: number
  /** Content pixels moved per scroll pixel. 1 is a plain scroller. */
  ramp: number
}) {
  const heights = Array.from({ length: options.lines }, (_, line) =>
    line % options.headingEvery === 0 ? options.headingHeight : options.bodyHeight)
  const tops: number[] = []
  let cursor = 0
  for (const height of heights) {
    tops.push(cursor)
    cursor += height
  }
  const contentHeight = cursor
  const maximum = Math.max(0, contentHeight / options.ramp - options.viewport)
  let top = 0
  let scrolls = 0
  const lineAt = (offset: number) => {
    let found = 0
    for (let line = 0; line < tops.length; line += 1) {
      if (tops[line] <= offset) found = line
      else break
    }
    return found
  }
  const view = (): LineWindow => {
    const offset = top * options.ramp
    const startLine = lineAt(offset)
    const endLine = lineAt(offset + options.viewport - 1)
    return { startLine, endLine: Math.max(startLine, endLine) }
  }
  const scroller: ViewportScroller = {
    window: view,
    top: () => top,
    scrollTo(next) {
      scrolls += 1
      top = Math.max(0, Math.min(maximum, next))
    },
    viewportHeight: () => options.viewport,
  }
  return {
    scroller,
    view,
    scrollCount: () => scrolls,
    setTop: (value: number) => { top = value },
    maximum,
  }
}

test('a jump lands the heading at the top of a viewport the host cannot measure', () => {
  const note = simulatedNote({
    lines: 900, viewport: 420, headingEvery: 12, bodyHeight: 18, headingHeight: 29, ramp: 1.14,
  })
  for (const target of [12, 96, 300, 587, 888]) {
    note.setTop(0)
    scrollLineIntoView(note.scroller, target)
    const view = note.view()
    assert.ok(view.startLine <= target && target <= view.endLine, `line ${target} is on screen`)
    // Landed at the top, unless the note has run out of scroll to give: the last screenful of
    // lines can only ever be shown where they are, and 888 of 900 is inside it.
    const clamped = note.scroller.top() === note.maximum
    assert.ok(target - view.startLine <= HEADING_JUMP.lead || clamped, `line ${target} is at the top`)
  }
})

test('a jump converges from either direction and in a handful of steps', () => {
  const note = simulatedNote({
    lines: 600, viewport: 400, headingEvery: 9, bodyHeight: 16, headingHeight: 32, ramp: 1.2,
  })
  // Backwards, from deep in the note to near its start.
  note.setTop(note.maximum)
  scrollLineIntoView(note.scroller, 40)
  const backwards = note.view()
  assert.ok(40 - backwards.startLine <= HEADING_JUMP.lead && 40 <= backwards.endLine)
  const before = note.scrollCount()
  scrollLineIntoView(note.scroller, 40)
  assert.equal(note.scrollCount(), before, 'a jump to where the reader already is scrolls nothing')
})

test('a heading too near the end to reach the top is still brought on screen', () => {
  const note = simulatedNote({
    lines: 200, viewport: 400, headingEvery: 10, bodyHeight: 20, headingHeight: 30, ramp: 1,
  })
  note.setTop(0)
  scrollLineIntoView(note.scroller, 199)
  const view = note.view()
  // The scroller is clamped at its floor, so the target cannot reach the top - but the loop must
  // stop there rather than spending its whole budget re-issuing a scroll that cannot move.
  assert.equal(note.scroller.top(), note.maximum)
  assert.ok(view.startLine <= 199 && 199 <= view.endLine)
})

test('an unmeasurable viewport is left alone', () => {
  let scrolls = 0
  const hidden: ViewportScroller = {
    window: () => null,
    top: () => 0,
    scrollTo: () => { scrolls += 1 },
    viewportHeight: () => 0,
  }
  scrollLineIntoView(hidden, 40)
  assert.equal(scrolls, 0, 'a display:none tab measures nothing and must not be scrolled blind')
})

test('the step size prefers what the last correction bought over any estimate', () => {
  assert.equal(seedLineHeight({ startLine: 0, endLine: 19 }, 400), 20)
  // A window holding one line taller than the viewport still yields a usable seed.
  assert.equal(seedLineHeight({ startLine: 7, endLine: 7 }, 400), 400)
  assert.equal(measuredLineHeight(600, 25, 20), 24)
  assert.equal(measuredLineHeight(-600, -25, 20), 24)
  // A move that changed the offset without changing the first visible line measures nothing.
  assert.equal(measuredLineHeight(600, 0, 20), 20)
  assert.equal(measuredLineHeight(0, 5, 20), 20)
  assert.equal(measuredLineHeight(1, 500, 20), HEADING_JUMP.minLineHeightPx)
})

test('a target already at the top is landed, and one hidden behind a tall line is not', () => {
  assert.equal(headingJumpDelta(10, { startLine: 9, endLine: 30 }, 20), 0)
  assert.equal(headingJumpDelta(10, { startLine: 10, endLine: 30 }, 20), 0)
  // Two rows down is one row too many; correct by exactly that row.
  assert.equal(headingJumpDelta(10, { startLine: 8, endLine: 30 }, 20), 20)
  assert.equal(headingJumpDelta(10, { startLine: 40, endLine: 60 }, 20), -620)
  // The line above the target wraps past the whole viewport, so the lead-in row is what is
  // hiding it. Giving the lead up aims at the top edge instead of reporting a false landing.
  assert.equal(headingJumpDelta(10, { startLine: 9, endLine: 9 }, 20), 20)
})
