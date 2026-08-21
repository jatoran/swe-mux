import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DROPDOWN_TYPEAHEAD_MS, dropdownIndexOf, dropdownScrollTop, firstDropdownIndex, isTypeAheadKey,
  lastDropdownIndex, nextDropdownIndex, nextTypeAhead, searchBuffer, typeAheadIndex,
  type DropdownOption,
} from '../src/dropdownOptions.ts'
import {
  DROPDOWN_MAX_HEIGHT_PX, DROPDOWN_MIN_HEIGHT_PX, DROPDOWN_MIN_WIDTH_PX,
  dropdownBox, dropdownCss,
} from '../src/dropdownPlacement.ts'

// The keyboard and geometry rules a native `<select>` gave the app for free, written down so
// the replacement can be held to them.

const options = (...labels: string[]): DropdownOption[] =>
  labels.map(label => ({ value: label.toLowerCase(), label }))

const VIEW = { left: 0, top: 0, width: 1280, height: 900 }

test('the arrows walk the list, wrap at both ends, and skip disabled rows', () => {
  const list: DropdownOption[] = [
    { value: 'a', label: 'Alpha' },
    { value: 'b', label: 'Beta', disabled: true },
    { value: 'c', label: 'Gamma' },
  ]
  assert.equal(nextDropdownIndex(list, -1, 1), 0, 'a first ArrowDown lands on the first row')
  assert.equal(nextDropdownIndex(list, 0, 1), 2, 'the disabled row is stepped over')
  assert.equal(nextDropdownIndex(list, 2, 1), 0, 'the end wraps to the start')
  assert.equal(nextDropdownIndex(list, 0, -1), 2, 'the start wraps to the end')
  assert.equal(nextDropdownIndex(list, -1, -1), 2, 'a first ArrowUp lands on the last row')
  assert.equal(firstDropdownIndex(list), 0)
  assert.equal(lastDropdownIndex(list), 2)
})

test('a list with nothing selectable reports no index rather than spinning', () => {
  const list: DropdownOption[] = [{ value: 'a', label: 'Alpha', disabled: true }]
  assert.equal(nextDropdownIndex(list, -1, 1), -1)
  assert.equal(firstDropdownIndex(list), -1)
  assert.equal(lastDropdownIndex(list), -1)
  assert.equal(nextDropdownIndex([], 0, 1), -1)
})

test('a value that is not in the list is reported as absent, not as the first row', () => {
  assert.equal(dropdownIndexOf(options('Alpha', 'Beta'), 'beta'), 1)
  assert.equal(dropdownIndexOf(options('Alpha', 'Beta'), 'gone'), -1)
})

test('type-ahead prefers a prefix over a substring, anywhere in the list', () => {
  const list = options('Disabled', 'Session started', 'Summary')
  // Scanning from -1 starts at index 0. "s" must not stop on "Disabled" just because that
  // row contains an s and comes first.
  assert.equal(typeAheadIndex(list, 's', -1), 1)
  assert.equal(typeAheadIndex(list, 'su', -1), 2)
  assert.equal(typeAheadIndex(list, 'sab', -1), 0, 'no prefix matches, so a substring may')
  assert.equal(typeAheadIndex(list, 'zzz', -1), -1, 'no match leaves the highlight alone')
  assert.equal(typeAheadIndex(list, '', 0), -1)
})

test('type-ahead cycles through the rows that share a prefix', () => {
  const list = options('Session A', 'Session B', 'Session C')
  assert.equal(typeAheadIndex(list, 's', 0), 1)
  assert.equal(typeAheadIndex(list, 's', 1), 2)
  assert.equal(typeAheadIndex(list, 's', 2), 0, 'and wraps back to the first')
})

test('a repeated letter is "the next one", not a two-letter prefix', () => {
  assert.equal(searchBuffer('ss'), 's')
  assert.equal(searchBuffer('sss'), 's')
  assert.equal(searchBuffer('se'), 'se', 'two different letters stay a prefix')
  assert.equal(searchBuffer('s'), 's')
})

test('the type-ahead buffer extends while typing and resets after the gap', () => {
  assert.equal(nextTypeAhead('se', 's', 200), 'ses')
  assert.equal(nextTypeAhead('se', 's', DROPDOWN_TYPEAHEAD_MS + 1), 's')
})

test('only an unmodified printable key is type-ahead', () => {
  assert.equal(isTypeAheadKey('s', false), true)
  assert.equal(isTypeAheadKey('s', true), false, 'Ctrl+S belongs to the app')
  assert.equal(isTypeAheadKey(' ', false), false, 'space chooses, as it does in a select')
  assert.equal(isTypeAheadKey('ArrowDown', false), false)
})

test('opening centres the current value; the arrows only scroll it back into view', () => {
  const metrics = { itemTop: 900, itemHeight: 30, viewHeight: 300, scrollHeight: 3000, scrollTop: 0 }
  assert.equal(dropdownScrollTop(metrics, 'centre'), 765)
  // Already visible: nothing moves, which is what keeps arrowing through a short list from
  // jittering the panel under the reader.
  assert.equal(dropdownScrollTop({ ...metrics, itemTop: 100, scrollTop: 50 }), 50)
  assert.equal(dropdownScrollTop({ ...metrics, itemTop: 20, scrollTop: 50 }), 20, 'above: to its top')
  assert.equal(dropdownScrollTop({ ...metrics, itemTop: 400, scrollTop: 50 }), 130, 'below: to its bottom')
  // And never past the ends, however the arithmetic lands.
  assert.equal(dropdownScrollTop({ ...metrics, itemTop: 0 }, 'centre'), 0)
  assert.equal(dropdownScrollTop({ ...metrics, itemTop: 2990 }, 'centre'), 2700)
})

test('the list opens below its trigger, at least as wide as it', () => {
  const box = dropdownBox({ left: 100, right: 340, top: 200, bottom: 224 }, VIEW)
  assert.equal(box.placement, 'below')
  assert.equal(box.left, 100)
  assert.equal(box.width, 240)
  assert.equal(box.edge, 226)
  assert.equal(box.maxHeight, DROPDOWN_MAX_HEIGHT_PX)
  assert.deepEqual(dropdownCss(box, 900), {
    left: '100px', width: '240px', 'max-height': '380px', top: '226px',
  })
})

test('a trigger low in the view flips the list above it', () => {
  const box = dropdownBox({ left: 100, right: 300, top: 840, bottom: 864 }, VIEW)
  assert.equal(box.placement, 'above')
  assert.equal(box.edge, 838, 'the panel hugs the trigger and grows upward from there')
  // The CSS inset is measured from the layout viewport, which the keyboard does not shrink.
  assert.deepEqual(dropdownCss(box, 900).bottom, '62px')
})

test('the list is its trigger\'s width, floored for a tiny one and capped on request', () => {
  // The default is the native popup's rule: the list and the control it belongs to are one
  // width, so a full-width settings row gets a full-width list.
  assert.equal(dropdownBox({ left: 0, right: 620, top: 10, bottom: 30 }, VIEW).width, 620)
  assert.equal(dropdownBox({ left: 10, right: 50, top: 10, bottom: 30 }, VIEW).width, DROPDOWN_MIN_WIDTH_PX)
  assert.equal(dropdownBox({ left: 0, right: 620, top: 10, bottom: 30 }, VIEW, { maxWidth: 300 }).width, 300)
  // And never wider than the view, whatever either of them says.
  assert.equal(dropdownBox({ left: 0, right: 4000, top: 10, bottom: 30 }, VIEW).width, VIEW.width - 12)
})

test('a list is clamped into the view rather than hanging off its edge', () => {
  const box = dropdownBox({ left: 1200, right: 1270, top: 10, bottom: 30 }, VIEW)
  assert.equal(box.left + box.width, VIEW.width - 6)
})

test('the visual viewport is what a soft keyboard shrinks, and the list respects it', () => {
  // Layout viewport still 900 tall; the keyboard leaves 420 visible. A trigger at y=380 has
  // room below only against the *visual* bound, and this is the whole reason placement is
  // measured against it rather than against `window.innerHeight`.
  const keyboard = { left: 0, top: 0, width: 390, height: 420 }
  const box = dropdownBox({ left: 20, right: 200, top: 360, bottom: 384 }, keyboard)
  assert.equal(box.placement, 'above', 'below the trigger is behind the keys')
  // And bounded by what is visible rather than by the desktop cap: 60% of 420, not 380.
  assert.equal(box.maxHeight, 252)
})

test('a view too short for either side keeps the roomier one and stays scrollable', () => {
  const squeezed = { left: 0, top: 0, width: 390, height: 200 }
  const low = dropdownBox({ left: 0, right: 100, top: 150, bottom: 174 }, squeezed)
  assert.equal(low.placement, 'above')
  assert.equal(low.maxHeight, DROPDOWN_MIN_HEIGHT_PX, 'a floor, so the list is never zero-height')
  const high = dropdownBox({ left: 0, right: 100, top: 10, bottom: 34 }, squeezed)
  assert.equal(high.placement, 'below')
})
