import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const css = readFileSync(join(import.meta.dirname, '..', 'src', 'style.css'), 'utf8')
// Comments talk *about* these selectors, including the one at the top of the file that
// explains the rule below. Only what the browser sees is under test.
const rules = css.replace(/\/\*[\s\S]*?\*\//g, '')

const TICK = /input:not\(\[type=["']?(checkbox|radio)["']?\]\)/g
const excludes = (selector: string, kind: 'checkbox' | 'radio') =>
  new RegExp(`:not\\(\\[type=["']?${kind}["']?\\]\\)`).test(selector)

/**
 * A checkbox and a radio are the same kind of thing to every container rule in this
 * file: a glyph-sized control that must keep the size `input[type=checkbox],
 * input[type=radio]` gives it, not the `width:100%` and 31px field height a text input
 * wants. Excluding one and not the other is the bug that keeps coming back - it shipped
 * twice in the settings panel, where it rendered every budget-mode radio as a 77x31 blob
 * and pushed the word beside it out of its own chip, on desktop as much as on a phone.
 *
 * The check is over the selector text rather than a rendered page because that is where
 * the mistake is made and because it catches the rule the day it is written, in a panel
 * no renderer test happens to mount.
 */
test('a container rule that spares the checkbox spares the radio too', () => {
  const offenders: string[] = []
  for (const block of rules.split('{')) {
    const selector = block.slice(block.lastIndexOf('}') + 1).trim()
    if (!selector || !TICK.test(selector)) { TICK.lastIndex = 0; continue }
    TICK.lastIndex = 0
    for (const part of selector.split(',')) {
      if (!TICK.test(part)) { TICK.lastIndex = 0; continue }
      TICK.lastIndex = 0
      if (!excludes(part, 'checkbox') || !excludes(part, 'radio')) offenders.push(part.trim())
    }
  }
  assert.deepEqual(
    offenders, [],
    `these selectors exclude one tick-sized control and not the other:\n  ${offenders.join('\n  ')}`,
  )
})

test('the one rule that sizes ticks and dots still covers both', () => {
  assert.match(
    rules,
    /input\[type=checkbox\],input\[type=radio\][^{]*\{[^}]*width:var\(--check-size\)/,
    'the shared sizing rule for checkboxes and radios is gone or no longer sizes them',
  )
})
