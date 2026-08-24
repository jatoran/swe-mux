import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { railClearancePx, RAIL_BOTTOM_TOLERANCE_PX } from '../src/railClearance.ts'

const VIEWPORT = 900

/** A rail of `height` px whose bottom edge is `gap` px above the bottom of the viewport. */
const rail = (height: number, gap = 0) => ({ top: VIEWPORT - gap - height, bottom: VIEWPORT - gap })

test('with no rail on screen nothing is lifted', () => {
  assert.equal(railClearancePx([], VIEWPORT), 0)
})

test('a rail at the bottom of the viewport is cleared by its own height', () => {
  assert.equal(railClearancePx([rail(34)], VIEWPORT), 34)
  // The mobile group is taller, and a two-row rail taller again. Neither number is written
  // down anywhere but the stylesheet, which is why the lift is measured rather than named.
  assert.equal(railClearancePx([rail(52)], VIEWPORT), 52)
  assert.equal(railClearancePx([rail(104)], VIEWPORT), 104)
})

test('a rail that does not reach the bottom edge is not a rail a toast can land on', () => {
  // The upper pane of a top/bottom split. Lifting a toast by its height would strand the
  // toast in the middle of the screen, over the terminal, clearing nothing.
  assert.equal(railClearancePx([rail(34, 300)], VIEWPORT), 0)
})

test('subpixel layout does not disqualify a rail', () => {
  assert.equal(railClearancePx([rail(34, RAIL_BOTTOM_TOLERANCE_PX)], VIEWPORT), 34 + RAIL_BOTTOM_TOLERANCE_PX)
  assert.equal(railClearancePx([rail(34, RAIL_BOTTOM_TOLERANCE_PX + 1)], VIEWPORT), 0)
})

test('several rails at the bottom edge lift by the tallest', () => {
  // A left/right split: both rails end at the bottom, and the toast sits over one of them.
  // Over-lifting the shorter one leaves a gap; under-lifting the taller one is the bug.
  assert.equal(railClearancePx([rail(34), rail(104), rail(52)], VIEWPORT), 104)
  // A rail that is not at the bottom contributes nothing even when it is the tallest.
  assert.equal(railClearancePx([rail(34), rail(400, 200)], VIEWPORT), 34)
})

test('a rail taller than the viewport cannot push a message off the top of the screen', () => {
  assert.equal(railClearancePx([{ top: -200, bottom: VIEWPORT }], VIEWPORT), VIEWPORT)
})

/** Every floating message pinned to the bottom of the viewport. Add a new one here: the
 *  list is the enforcement point, because a surface that forgets `--rail-clearance` lands
 *  on the command rail exactly the way the selection readout did and nothing else looks. */
const BOTTOM_ANCHORED = ['.toast-stack', '.notification-toast', '.interaction-hud']

test('every message pinned to the bottom of the viewport clears the rail', () => {
  const css = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  // Declaration blocks, one selector at a time — the sheet packs many rules onto a line, and
  // the mobile override of `.notification-toast` sets `bottom` without restating `position`.
  const rules: string[] = css.match(/[^{}]*\{[^{}]*\}/g) ?? []
  for (const selector of BOTTOM_ANCHORED) {
    // The head can start mid-line, right after a preceding rule's `}` or a comment's `*/`,
    // so the left boundary is "not a selector character" rather than whitespace.
    const named = new RegExp(`(^|[^\\w.-])\\${selector}([\\s,{:]|$)`)
    const owned = rules.filter(rule => named.test(rule.slice(0, rule.indexOf('{'))) && /bottom:/.test(rule))
    assert.ok(owned.length > 0, `${selector} should declare a bottom offset`)
    for (const rule of owned) {
      assert.match(rule, /bottom:[^;}]*var\(--rail-clearance\)/, `${selector} must clear the rail: ${rule}`)
    }
  }
})
