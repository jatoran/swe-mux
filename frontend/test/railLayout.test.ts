import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultRailConfig, resolveRailRows, type RailConfig } from '../src/commandRail.ts'
import {
  addRailCatalogItem, addRailRow, copyRailSurface, deleteRailCatalogItem, dropIndexForPoint,
  duplicateRailEntry, insertRailItem, moveRailEntry, moveRailRow, railEntryId, railPlacementCounts,
  removeRailEntry, removeRailRow, sameRailRef, setRailRowLabel, toggleRailPlacement,
  updateRailCatalogItem, type RailChipRect,
} from '../src/railLayout.ts'

/** A small, predictable config: two strip rows on desktop, one on mobile. */
function fixture(): RailConfig {
  const config = defaultRailConfig()
  config.layouts.desktop.strip = [{ id: 'd1', items: ['esc', 'enter', 'tab'] }, { id: 'd2', items: ['up', 'down'] }]
  config.layouts.desktop.panel = [{ id: 'dp', items: ['home'] }]
  config.layouts.mobile.strip = [{ id: 'm1', items: ['esc'] }]
  config.layouts.mobile.panel = [{ id: 'mp', items: [] }]
  return config
}

const strip = (config: RailConfig, device: 'desktop' | 'mobile' = 'desktop'): string[][] =>
  config.layouts[device].strip.map(row => [...row.items])

test('every operation returns a new config and leaves the input untouched', () => {
  const before = fixture()
  const snapshot = JSON.stringify(before)
  removeRailEntry(before, { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 })
  insertRailItem(before, 'left', { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 })
  addRailRow(before, 'mobile', 'panel')
  toggleRailPlacement(before, 'left', 'mobile', 'strip')
  assert.equal(JSON.stringify(before), snapshot)
})

test('moving within a row uses an index measured without the dragged chip', () => {
  // Dragging `esc` (index 0) past `enter` puts the pointer after one peer, so the
  // target index is 1 — which must land it between `enter` and `tab`, not after `tab`.
  const next = moveRailEntry(fixture(),
    { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 },
    { device: 'desktop', surface: 'strip', rowId: 'd1', index: 1 })
  assert.deepEqual(strip(next)[0], ['enter', 'esc', 'tab'])
})

test('moving to the end of its own row appends rather than overshooting', () => {
  const next = moveRailEntry(fixture(),
    { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 },
    { device: 'desktop', surface: 'strip', rowId: 'd1', index: 2 })
  assert.deepEqual(strip(next)[0], ['enter', 'tab', 'esc'])
})

test('a chip moves between rows, between surfaces, and between devices', () => {
  let config = moveRailEntry(fixture(),
    { device: 'desktop', surface: 'strip', rowId: 'd1', index: 1 },
    { device: 'desktop', surface: 'strip', rowId: 'd2', index: 0 })
  assert.deepEqual(strip(config), [['esc', 'tab'], ['enter', 'up', 'down']])

  config = moveRailEntry(config,
    { device: 'desktop', surface: 'strip', rowId: 'd2', index: 0 },
    { device: 'desktop', surface: 'panel', rowId: 'dp', index: 0 })
  assert.deepEqual(config.layouts.desktop.panel[0].items, ['enter', 'home'])

  config = moveRailEntry(config,
    { device: 'desktop', surface: 'panel', rowId: 'dp', index: 0 },
    { device: 'mobile', surface: 'strip', rowId: 'm1', index: 1 })
  assert.deepEqual(strip(config, 'mobile'), [['esc', 'enter']])
  assert.deepEqual(config.layouts.desktop.panel[0].items, ['home'])
})

test('an out-of-range drop index appends instead of dropping the item', () => {
  const next = insertRailItem(fixture(), 'left', { device: 'mobile', surface: 'strip', rowId: 'm1', index: 99 })
  assert.deepEqual(strip(next, 'mobile'), [['esc', 'left']])
})

test('a chip can be duplicated, and duplicates resolve as separate buttons', () => {
  const next = duplicateRailEntry(fixture(), { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 })
  assert.deepEqual(strip(next)[0], ['esc', 'esc', 'enter', 'tab'])
  const entries = resolveRailRows(next, 'strip', { device: 'desktop', backend: 'claude' })[0].entries
  assert.equal(entries.filter(entry => entry.item.id === 'esc').length, 2)
  assert.equal(new Set(entries.map(entry => entry.key)).size, entries.length)
})

test('placement counts see every copy on every surface', () => {
  const config = duplicateRailEntry(fixture(), { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 })
  assert.deepEqual(railPlacementCounts(config, 'esc'), {
    desktop: { strip: 2, panel: 0 },
    mobile: { strip: 1, panel: 0 },
  })
  assert.deepEqual(railPlacementCounts(config, 'rewind'), {
    desktop: { strip: 0, panel: 0 },
    mobile: { strip: 0, panel: 0 },
  })
})

test('toggling a placement adds once and removes every copy', () => {
  let config = duplicateRailEntry(fixture(), { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 })
  config = toggleRailPlacement(config, 'esc', 'desktop', 'strip')
  assert.equal(railPlacementCounts(config, 'esc').desktop.strip, 0)
  // The other device is untouched: the two layouts never move together.
  assert.equal(railPlacementCounts(config, 'esc').mobile.strip, 1)
  config = toggleRailPlacement(config, 'esc', 'desktop', 'strip')
  assert.equal(railPlacementCounts(config, 'esc').desktop.strip, 1)
  // It lands in the last row of that surface, ready to be dragged.
  assert.deepEqual(config.layouts.desktop.strip.at(-1)?.items.at(-1), 'esc')
})

test('rows can be added, labelled, reordered, and removed', () => {
  let config = addRailRow(fixture(), 'mobile', 'strip')
  assert.equal(config.layouts.mobile.strip.length, 2)
  const added = config.layouts.mobile.strip[1].id
  config = setRailRowLabel(config, 'mobile', 'strip', added, '  keys  ')
  assert.equal(config.layouts.mobile.strip[1].label, 'keys')
  config = setRailRowLabel(config, 'mobile', 'strip', added, '   ')
  assert.equal('label' in config.layouts.mobile.strip[1], false)
  config = moveRailRow(config, 'mobile', 'strip', added, -1)
  assert.equal(config.layouts.mobile.strip[0].id, added)
  config = moveRailRow(config, 'mobile', 'strip', added, -1)
  assert.equal(config.layouts.mobile.strip[0].id, added, 'a move past the ends is a no-op')
  config = removeRailRow(config, 'mobile', 'strip', added)
  assert.equal(config.layouts.mobile.strip.length, 1)
})

test('removing the only row of a surface empties it instead', () => {
  // A surface with no rows would have nowhere to drop and nowhere for a newly
  // shipped built-in to land.
  const config = removeRailRow(fixture(), 'mobile', 'strip', 'm1')
  assert.equal(config.layouts.mobile.strip.length, 1)
  assert.deepEqual(config.layouts.mobile.strip[0].items, [])
})

test('copying a surface is a one-shot with fresh row ids', () => {
  const config = copyRailSurface(fixture(), 'desktop', 'mobile', 'strip')
  assert.deepEqual(strip(config, 'mobile'), [['esc', 'enter', 'tab'], ['up', 'down']])
  // Distinct row ids, so a later edit to one device cannot address the other's row.
  const desktopIds = config.layouts.desktop.strip.map(row => row.id)
  for (const row of config.layouts.mobile.strip) assert.equal(desktopIds.includes(row.id), false)
  // And the copy does not track: editing the source leaves the copy alone.
  const edited = removeRailEntry(config, { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 })
  assert.deepEqual(strip(edited, 'mobile')[0], ['esc', 'enter', 'tab'])
})

test('copying only touches the named surface, and never a device onto itself', () => {
  const config = copyRailSurface(fixture(), 'desktop', 'mobile', 'strip')
  assert.deepEqual(config.layouts.mobile.panel[0].items, [])
  assert.equal(copyRailSurface(config, 'mobile', 'mobile', 'strip'), config)
})

test('adding a command places it on both devices', () => {
  const config = addRailCatalogItem(fixture(), { id: 'custom:skill:ship', type: 'skill', label: 'ship', text: 'ship', defaultSurface: 'panel' })
  assert.equal(config.items.some(item => item.id === 'custom:skill:ship'), true)
  assert.deepEqual(railPlacementCounts(config, 'custom:skill:ship'), {
    desktop: { strip: 0, panel: 1 },
    mobile: { strip: 0, panel: 1 },
  })
})

test('adding a duplicate id is refused rather than shadowing the original', () => {
  const config = fixture()
  assert.equal(addRailCatalogItem(config, { id: 'esc', type: 'text', label: 'nope', text: 'x' }), config)
})

test('deleting a custom command takes every button pointing at it', () => {
  let config = addRailCatalogItem(fixture(), { id: 'custom:text:hi', type: 'text', label: 'hi', text: 'hi', defaultSurface: 'strip' })
  config = duplicateRailEntry(config, { device: 'desktop', surface: 'strip', rowId: 'd2', index: 2 })
  assert.equal(railPlacementCounts(config, 'custom:text:hi').desktop.strip, 2)
  config = deleteRailCatalogItem(config, 'custom:text:hi')
  assert.equal(config.items.some(item => item.id === 'custom:text:hi'), false)
  assert.deepEqual(railPlacementCounts(config, 'custom:text:hi'), {
    desktop: { strip: 0, panel: 0 },
    mobile: { strip: 0, panel: 0 },
  })
})

test('built-ins are app-owned: they cannot be deleted or edited, only unplaced', () => {
  const config = fixture()
  assert.equal(deleteRailCatalogItem(config, 'esc'), config)
  assert.equal(updateRailCatalogItem(config, 'esc', { label: 'nope' }), config)
  const unplaced = toggleRailPlacement(toggleRailPlacement(config, 'esc', 'desktop', 'strip'), 'esc', 'mobile', 'strip')
  assert.deepEqual(railPlacementCounts(unplaced, 'esc'), {
    desktop: { strip: 0, panel: 0 },
    mobile: { strip: 0, panel: 0 },
  })
  assert.equal(unplaced.items.some(item => item.id === 'esc'), true)
})

test('a custom command keeps its id when its fields are edited', () => {
  let config = addRailCatalogItem(fixture(), { id: 'custom:slash:new', type: 'slash', label: 'new', text: 'new' })
  config = updateRailCatalogItem(config, 'custom:slash:new', { label: 'fresh', id: 'hijack' })
  const item = config.items.find(entry => entry.id === 'custom:slash:new')
  assert.equal(item?.label, 'fresh')
  assert.equal(config.items.some(entry => entry.id === 'hijack'), false)
})

test('refs address positions, and stale ones resolve to nothing', () => {
  const config = fixture()
  assert.equal(railEntryId(config, { device: 'desktop', surface: 'strip', rowId: 'd1', index: 2 }), 'tab')
  assert.equal(railEntryId(config, { device: 'desktop', surface: 'strip', rowId: 'd1', index: 9 }), null)
  assert.equal(railEntryId(config, { device: 'desktop', surface: 'strip', rowId: 'gone', index: 0 }), null)
  const ref = { device: 'desktop', surface: 'strip', rowId: 'd1', index: 0 } as const
  assert.equal(sameRailRef(ref, { ...ref }), true)
  assert.equal(sameRailRef(ref, { ...ref, index: 1 }), false)
  assert.equal(sameRailRef(ref, null), false)
  // A move from a ref that no longer points anywhere is a no-op, not a crash.
  assert.equal(moveRailEntry(config, { ...ref, index: 9 }, { ...ref, index: 0 }), config)
})

// --- hit testing -----------------------------------------------------------

const line = (keys: string[], top: number, width = 100): RailChipRect[] =>
  keys.map((key, index) => ({ key, left: index * width, right: (index + 1) * width, top, bottom: top + 30 }))

test('a drop index counts the chips that come before the pointer', () => {
  const rects = line(['a', 'b', 'c'], 0)
  assert.equal(dropIndexForPoint(rects, null, 10, 15), 0)
  assert.equal(dropIndexForPoint(rects, null, 90, 15), 1)
  assert.equal(dropIndexForPoint(rects, null, 160, 15), 2)
  assert.equal(dropIndexForPoint(rects, null, 290, 15), 3)
})

test('the dragged chip is excluded, so its index is measured against its peers', () => {
  const rects = line(['a', 'b', 'c'], 0)
  // Hovering the right half of `b` while dragging `a`: one peer (`b`) precedes,
  // so the index is 1 — into the list that no longer contains `a`.
  assert.equal(dropIndexForPoint(rects, 'a', 160, 15), 1)
  // Hovering its own slot is a no-op position rather than a shifted one.
  assert.equal(dropIndexForPoint(rects, 'a', 10, 15), 0)
})

test('a wrapped row is measured in reading order, not by x alone', () => {
  // Two visual lines: a/b on the first, c/d on the second. A pointer far left on
  // the second line must land after b, which a horizontal-only test would miss.
  const rects = [...line(['a', 'b'], 0), ...line(['c', 'd'], 40)]
  assert.equal(dropIndexForPoint(rects, null, 10, 55), 2)
  assert.equal(dropIndexForPoint(rects, null, 120, 55), 3)
  assert.equal(dropIndexForPoint(rects, null, 190, 55), 4)
  assert.equal(dropIndexForPoint(rects, null, 10, 15), 0)
  // Below every line: append.
  assert.equal(dropIndexForPoint(rects, null, 10, 200), 4)
})

test('an empty row always accepts a drop at index zero', () => {
  assert.equal(dropIndexForPoint([], null, 400, 400), 0)
  assert.equal(dropIndexForPoint(line(['a'], 0), 'a', 400, 400), 0)
})
