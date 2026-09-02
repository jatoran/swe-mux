import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { defaultRailConfig, resolveRailRows, type RailConfig, type RailItem } from '../src/commandRail.ts'
import {
  applyRailArrange, caretPosition, chipBandY, pruneEmptyRailRows, railArrangeScopeDetail,
  railArrangeScopeLabel, slotsWithoutDragged, storedInsertIndex,
} from '../src/railArrange.ts'
import { dropIndexForPoint, type RailChipRect } from '../src/railLayout.ts'

const key = (id: string, backends?: string[]): RailItem => ({ id, type: 'key', label: id.toUpperCase(), bytes: id, ...(backends ? { backends } : {}) })

/**
 * Three chips in one row, of which the middle one belongs to another harness, plus a second
 * row. This is the shape the whole module exists for: a shell session draws `a` and `c` side
 * by side, so a drop "between the two chips I can see" is stored index 1, not 1-by-accident.
 */
function fixture(): RailConfig {
  const config = defaultRailConfig()
  config.items = [key('a'), key('b', ['claude']), key('c'), key('d'), key('spare')]
  config.layouts.desktop.strip = [{ id: 'r1', items: ['a', 'b', 'c'] }, { id: 'r2', items: ['d'] }]
  config.layouts.mobile.strip = [{ id: 'm1', items: [] }]
  return config
}

const strip = (config: RailConfig, device: 'desktop' | 'mobile' = 'desktop'): string[][] =>
  config.layouts[device].strip.map(row => [...row.items])

// ---------------------------------------------------------------------------
// Rendered coordinates to stored ones
// ---------------------------------------------------------------------------

test('a drop before a rendered chip lands before the slot that chip occupies', () => {
  // `a` at 0 and `c` at 2 are rendered; `b` at 1 is filtered out of this session.
  assert.equal(storedInsertIndex([0, 2], 0, 3), 0)
  assert.equal(storedInsertIndex([0, 2], 1, 3), 2)
})

test('a drop past the last rendered chip appends to the row rather than to what is drawn', () => {
  // Not 3 by luck: past the end is the row's end, so a trailing filtered item stays trailing.
  assert.equal(storedInsertIndex([0, 2], 2, 4), 4)
  assert.equal(storedInsertIndex([], 0, 2), 2)
  assert.equal(storedInsertIndex([0], 7, 5), 5)
})

test('a non-finite rendered index appends instead of producing NaN', () => {
  assert.equal(storedInsertIndex([0, 1], Number.NaN, 2), 2)
})

test('slots are renumbered only when the dragged chip is leaving this row', () => {
  assert.deepEqual(slotsWithoutDragged([0, 2, 4], null), [0, 2, 4])
  assert.deepEqual(slotsWithoutDragged([0, 2, 4], 2), [0, 3])
  assert.deepEqual(slotsWithoutDragged([0, 2, 4], 0), [1, 3])
})

test('the caret steps over the dragged chip, which stays in the row', () => {
  assert.equal(caretPosition(0, -1), 0)
  assert.equal(caretPosition(2, -1), 2)
  // Dragged chip drawn at 1: peers 0 -> 0, and peer 1 (drawn at 2) -> 2.
  assert.equal(caretPosition(0, 1), 0)
  assert.equal(caretPosition(1, 1), 2)
  assert.equal(caretPosition(2, 1), 3)
})

test('a point outside the chips is measured against the band they occupy', () => {
  const rects = [{ top: 10, bottom: 30 }, { top: 10, bottom: 30 }]
  assert.equal(chipBandY(rects, 20), 20)
  assert.equal(chipBandY(rects, 2), 10)
  assert.equal(chipBandY(rects, 99), 30)
  assert.equal(chipBandY([], 99), 99)
})

test('the band clamp is what keeps a drop in the strip padding off the ends of the row', () => {
  // One line of three chips, and a pointer in the strip's own vertical padding. Unclamped,
  // `dropIndexForPoint` falls back to comparing row midpoints - which every chip on a
  // single-line strip shares - so the answer is an end of the row rather than the gap the
  // pointer is squarely between, and which end depends only on which side of the line it is.
  const rects: RailChipRect[] = [
    { key: '0', left: 0, right: 20, top: 10, bottom: 30 },
    { key: '1', left: 20, right: 40, top: 10, bottom: 30 },
    { key: '2', left: 40, right: 60, top: 10, bottom: 30 },
  ]
  assert.equal(dropIndexForPoint(rects, null, 30, 4), 0, 'above the line, unclamped, reads as the start of the row')
  assert.equal(dropIndexForPoint(rects, null, 30, 44), 3, 'below the line, unclamped, reads as past the end')
  assert.equal(dropIndexForPoint(rects, null, 30, chipBandY(rects, 4)), 1)
  assert.equal(dropIndexForPoint(rects, null, 30, chipBandY(rects, 44)), 1)
})

// ---------------------------------------------------------------------------
// Empty rows
// ---------------------------------------------------------------------------

test('an emptied row is pruned, a labelled one is kept, and a surface always keeps a row', () => {
  const config = fixture()
  config.layouts.desktop.strip = [{ id: 'r1', items: [] }, { id: 'r2', items: ['d'] }, { id: 'r3', items: [], label: 'Keys' }]
  const pruned = pruneEmptyRailRows(config, 'desktop', 'strip')
  assert.deepEqual(pruned.layouts.desktop.strip.map(row => row.id), ['r2', 'r3'])
  const last = pruneEmptyRailRows({ ...config, layouts: { ...config.layouts, desktop: { strip: [{ id: 'only', items: [] }] } } }, 'desktop', 'strip')
  assert.deepEqual(last.layouts.desktop.strip.map(row => row.id), ['only'])
})

test('a row emptied only by backend filtering is not pruned', () => {
  const config = fixture()
  config.layouts.desktop.strip = [{ id: 'r1', items: ['b'] }]
  assert.equal(pruneEmptyRailRows(config, 'desktop', 'strip').layouts.desktop.strip.length, 1)
})

test('pruning returns the same object when there is nothing to prune', () => {
  const config = fixture()
  assert.equal(pruneEmptyRailRows(config, 'desktop', 'strip'), config)
})

test('resolveRailRows keeps empty rows only when asked, and never for a reading path', () => {
  const config = fixture()
  const ctx = { device: 'desktop' as const, backend: 'shell' }
  assert.deepEqual(resolveRailRows(config, 'strip', ctx).map(row => row.id), ['r1', 'r2'])
  config.layouts.desktop.strip = [{ id: 'r1', items: ['b'] }, { id: 'r2', items: ['d'] }]
  assert.deepEqual(resolveRailRows(config, 'strip', ctx).map(row => row.id), ['r2'])
  assert.deepEqual(resolveRailRows(config, 'strip', ctx, true).map(row => row.id), ['r1', 'r2'])
})

// ---------------------------------------------------------------------------
// Applying a drop
// ---------------------------------------------------------------------------

const ref = (rowId: string, index: number) => ({ device: 'desktop' as const, surface: 'strip' as const, rowId, index })
const apply = (config: RailConfig, source: Parameters<typeof applyRailArrange>[3], target: Parameters<typeof applyRailArrange>[4]) =>
  applyRailArrange(config, 'desktop', 'strip', source, target)

test('a drop leaves the input config untouched', () => {
  const config = fixture()
  const snapshot = JSON.stringify(config)
  apply(config, { kind: 'chip', ref: ref('r1', 0) }, { kind: 'row', rowId: 'r2', index: 0 })
  apply(config, { kind: 'chip', ref: ref('r1', 0) }, { kind: 'remove' })
  apply(config, { kind: 'catalog', itemId: 'spare' }, { kind: 'new-row' })
  assert.equal(JSON.stringify(config), snapshot)
})

test('moving a chip within its row uses an index measured with the chip already gone', () => {
  const next = apply(fixture(), { kind: 'chip', ref: ref('r1', 0) }, { kind: 'row', rowId: 'r1', index: 2 })
  assert.deepEqual(strip(next!), [['b', 'c', 'a'], ['d']])
})

test('a chip dropped into another row leaves the first', () => {
  const next = apply(fixture(), { kind: 'chip', ref: ref('r1', 2) }, { kind: 'row', rowId: 'r2', index: 0 })
  assert.deepEqual(strip(next!), [['a', 'b'], ['c', 'd']])
})

test('the last chip out of a row takes the row with it', () => {
  const next = apply(fixture(), { kind: 'chip', ref: ref('r2', 0) }, { kind: 'row', rowId: 'r1', index: 0 })
  assert.deepEqual(strip(next!), [['d', 'a', 'b', 'c']])
})

test('a catalog entry is placed without being taken from anywhere', () => {
  const next = apply(fixture(), { kind: 'catalog', itemId: 'spare' }, { kind: 'row', rowId: 'r1', index: 1 })
  assert.deepEqual(strip(next!), [['a', 'spare', 'b', 'c'], ['d']])
})

test('a chip dropped on the bin is removed', () => {
  const next = apply(fixture(), { kind: 'chip', ref: ref('r1', 1) }, { kind: 'remove' })
  assert.deepEqual(strip(next!), [['a', 'c'], ['d']])
})

test('a catalog entry dropped on the bin is an abort, not an error', () => {
  assert.equal(apply(fixture(), { kind: 'catalog', itemId: 'spare' }, { kind: 'remove' }), null)
})

test('the new-row target creates a row and puts the chip in it', () => {
  const next = apply(fixture(), { kind: 'chip', ref: ref('r1', 0) }, { kind: 'new-row' })
  assert.deepEqual(strip(next!), [['b', 'c'], ['d'], ['a']])
})

test('a new row made from the last chip of a row does not leave the old one behind', () => {
  const next = apply(fixture(), { kind: 'chip', ref: ref('r2', 0) }, { kind: 'new-row' })
  assert.deepEqual(strip(next!), [['a', 'b', 'c'], ['d']])
})

test('a drop that means nothing returns null rather than an identical config', () => {
  assert.equal(apply(fixture(), { kind: 'chip', ref: ref('r1', 9) }, { kind: 'remove' }), null)
  assert.equal(apply(fixture(), { kind: 'catalog', itemId: 'nonesuch' }, { kind: 'row', rowId: 'r1', index: 0 }), null)
  assert.equal(apply(fixture(), { kind: 'catalog', itemId: 'spare' }, { kind: 'row', rowId: 'gone', index: 0 }), null)
})

test('a filtered neighbour keeps its place when the chip beside it is moved', () => {
  // A shell session sees `a c`. Dropping `d` between them is rendered index 1, which the
  // translation turns into stored index 2 - after the invisible `b`, exactly where it looks.
  const config = fixture()
  const rendered = resolveRailRows(config, 'strip', { device: 'desktop', backend: 'shell' })
  const slots = rendered[0].entries.map(entry => entry.index)
  assert.deepEqual(slots, [0, 2])
  const index = storedInsertIndex(slotsWithoutDragged(slots, null), 1, 3)
  const next = apply(config, { kind: 'chip', ref: ref('r2', 0) }, { kind: 'row', rowId: 'r1', index })
  assert.deepEqual(strip(next!), [['a', 'b', 'd', 'c']])
})

// ---------------------------------------------------------------------------
// Scope
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// The DOM contract the drag reads
// ---------------------------------------------------------------------------

test('every chip shape publishes its stored slot and its occurrence key', () => {
  // The renderer harness draws plain buttons, so it cannot notice a chip *shape* that stopped
  // publishing these - and a chip with no `data-rail-slot` is not a drag that fails, it is a
  // chip the hit test skips, which silently shifts every index measured past it.
  const pane = readFileSync(new URL('../src/TerminalPane.tsx', import.meta.url), 'utf8')
  for (const shape of [/<RailPad [^>]*slot=\{index\} reorderId=\{key\}/, /<RailRepeatKey [^>]*slot=\{index\} reorderId=\{key\}/]) {
    assert.match(pane, shape)
  }
  assert.match(pane, /data-rail-slot=\{index\}\s*\n\s*data-reorder-id=\{key\}/)
  for (const source of ['../src/RailPad.tsx', '../src/RailRepeatKey.tsx']) {
    const text = readFileSync(new URL(source, import.meta.url), 'utf8')
    assert.match(text, /data-rail-slot=\{slot\}/, source)
    assert.match(text, /data-reorder-id=\{reorderId\}/, source)
  }
})

test('the chips of an arranging rail take no pointer events', () => {
  // The whole reason arrangement is a mode: these are the production buttons, and a pad
  // opens its fan from the first pixel of a drag while an arrow repeats on a hold. Taking
  // their pointer events away is what makes a rearranging gesture unambiguous, rather than
  // asking each of them to recognise a mode.
  const styles = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  assert.match(styles, /\.rail-arranging \.terminal-action-scroll>\*[^{]*\{pointer-events:none\}/)
  assert.match(styles, /\.rail-arranging \.rail-arrange-grid>\*/)
  assert.match(styles, /\.rail-arrange-catalog-chip>\*/)
})

test('a hold anywhere on the rail is a gesture rather than a selection', () => {
  // A platform long-press over non-editable content moves focus, and on Android a focus move
  // is what lowers the soft keyboard. Only the repeating arrow keys used to say this, so a
  // slow press on any other chip closed the keyboard and a quick one did not.
  const styles = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  const rule = /\.terminal-action-rail button,\.rail-arrange-catalog-chip\{([^}]*)\}/.exec(styles)?.[1] ?? ''
  assert.match(rule, /user-select:none/)
  assert.match(rule, /-webkit-touch-callout:none/)
})

// ---------------------------------------------------------------------------
// Scope
// ---------------------------------------------------------------------------

test('only a detached Project says it is editing its own rail', () => {
  assert.equal(railArrangeScopeLabel('global'), 'Editing the global rail')
  assert.equal(railArrangeScopeLabel('delta'), 'Editing the global rail')
  assert.equal(railArrangeScopeLabel('fork'), 'Editing this Project’s rail')
  assert.notEqual(railArrangeScopeDetail('delta'), railArrangeScopeDetail('global'))
  assert.match(railArrangeScopeDetail('fork'), /detached/)
})
