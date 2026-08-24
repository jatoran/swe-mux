import assert from 'node:assert/strict'
import test from 'node:test'
import { applySelectionClick, type SelectableRow, type Selection } from '../src/worktreeSelection.ts'

const rows = (...specs: (string | [string, boolean])[]): SelectableRow[] =>
  specs.map(spec =>
    typeof spec === 'string' ? { path: spec, selectable: true } : { path: spec[0], selectable: spec[1] },
  )

const LIST = rows('D:\\wt\\a', 'D:\\wt\\b', 'D:\\wt\\c', 'D:\\wt\\d')
const keys = (selection: Selection) => Object.keys(selection).sort()

const plain = (selection: Selection, list: SelectableRow[], path: string, anchor = '') =>
  applySelectionClick(selection, list, path, { extend: false, anchor })
const shift = (selection: Selection, list: SelectableRow[], path: string, anchor: string) =>
  applySelectionClick(selection, list, path, { extend: true, anchor })

test('a plain click toggles one row and becomes the anchor', () => {
  const first = plain({}, LIST, 'D:\\wt\\b')
  assert.deepEqual(keys(first.selected), ['d:/wt/b'])
  assert.equal(first.anchor, 'd:/wt/b')

  const second = plain(first.selected, LIST, 'D:\\wt\\b', first.anchor)
  assert.deepEqual(keys(second.selected), [])
  assert.equal(second.anchor, 'd:/wt/b')
})

test('a shift click selects everything between the anchor and the click, inclusive', () => {
  const anchored = plain({}, LIST, 'D:\\wt\\a')
  const ranged = shift(anchored.selected, LIST, 'D:\\wt\\c', anchored.anchor)
  assert.deepEqual(keys(ranged.selected), ['d:/wt/a', 'd:/wt/b', 'd:/wt/c'])
})

test('the range runs in either direction', () => {
  const anchored = plain({}, LIST, 'D:\\wt\\d')
  const ranged = shift(anchored.selected, LIST, 'D:\\wt\\b', anchored.anchor)
  assert.deepEqual(keys(ranged.selected), ['d:/wt/b', 'd:/wt/c', 'd:/wt/d'])
})

test('a shift click on a checked box un-selects the range instead', () => {
  const all = { 'd:/wt/a': true, 'd:/wt/b': true, 'd:/wt/c': true, 'd:/wt/d': true } as const
  const ranged = shift(all, LIST, 'D:\\wt\\c', 'd:/wt/a')
  assert.deepEqual(keys(ranged.selected), ['d:/wt/d'])
})

test('a blocked row inside the range is stepped over, not pressed', () => {
  const list = rows('D:\\wt\\a', ['D:\\wt\\b', false], 'D:\\wt\\c')
  const ranged = shift({ 'd:/wt/a': true }, list, 'D:\\wt\\c', 'd:/wt/a')
  assert.deepEqual(keys(ranged.selected), ['d:/wt/a', 'd:/wt/c'])
})

test('a run of shift clicks walks the list from each press', () => {
  const anchored = plain({}, LIST, 'D:\\wt\\a')
  const first = shift(anchored.selected, LIST, 'D:\\wt\\b', anchored.anchor)
  assert.equal(first.anchor, 'd:/wt/b')
  const second = shift(first.selected, LIST, 'D:\\wt\\d', first.anchor)
  assert.deepEqual(keys(second.selected), ['d:/wt/a', 'd:/wt/b', 'd:/wt/c', 'd:/wt/d'])
  assert.equal(second.anchor, 'd:/wt/d')
})

test('shift clicking back over an overshoot un-selects it', () => {
  const anchored = plain({}, LIST, 'D:\\wt\\a')
  const wide = shift(anchored.selected, LIST, 'D:\\wt\\d', anchored.anchor)
  assert.deepEqual(keys(wide.selected), ['d:/wt/a', 'd:/wt/b', 'd:/wt/c', 'd:/wt/d'])
  // `b` is checked, so the press un-checks it, and the range back to the last box
  // touched (`d`) goes with it. What is left is the range the reader meant.
  const corrected = shift(wide.selected, LIST, 'D:\\wt\\b', wide.anchor)
  assert.deepEqual(keys(corrected.selected), ['d:/wt/a'])
})

test('a shift click with no anchor is a plain click', () => {
  const result = shift({}, LIST, 'D:\\wt\\c', '')
  assert.deepEqual(keys(result.selected), ['d:/wt/c'])
  assert.equal(result.anchor, 'd:/wt/c')
})

test('an anchor the search box has filtered away degrades to a plain click', () => {
  // The reader typed a filter after anchoring on `a`; `a` is no longer on screen, so
  // there is no visible range to sweep and nothing may be selected out of sight.
  const visible = rows('D:\\wt\\c', 'D:\\wt\\d')
  const result = shift({ 'd:/wt/a': true }, visible, 'D:\\wt\\d', 'd:/wt/a')
  assert.deepEqual(keys(result.selected), ['d:/wt/a', 'd:/wt/d'])
  assert.equal(result.anchor, 'd:/wt/d')
})

test('the range spans the visible order, never the rows a filter hid', () => {
  const visible = rows('D:\\wt\\a', 'D:\\wt\\d')
  const result = shift({ 'd:/wt/a': true }, visible, 'D:\\wt\\d', 'd:/wt/a')
  assert.deepEqual(keys(result.selected), ['d:/wt/a', 'd:/wt/d'])
})

test('shift clicking the anchor itself is a plain toggle of that row', () => {
  const result = shift({ 'd:/wt/b': true }, LIST, 'D:\\wt\\b', 'd:/wt/b')
  assert.deepEqual(keys(result.selected), [])
  assert.equal(result.anchor, 'd:/wt/b')
})

test('the clicked row always moves, even when the walk would have skipped it', () => {
  // A row the caller marked unselectable cannot be clicked in the UI, but if one ever
  // is, the state must follow the box the browser has already ticked.
  const list = rows('D:\\wt\\a', ['D:\\wt\\c', false])
  const result = shift({ 'd:/wt/a': true }, list, 'D:\\wt\\c', 'd:/wt/a')
  assert.deepEqual(keys(result.selected), ['d:/wt/a', 'd:/wt/c'])
})

test('paths are matched normalized, so a separator or case difference cannot hide a row', () => {
  const list = rows('D:\\WT\\A', 'd:/wt/b', 'D:\\wt\\c\\')
  const anchored = plain({}, list, 'd:/WT/a')
  assert.equal(anchored.anchor, 'd:/wt/a')
  const ranged = shift(anchored.selected, list, 'D:\\wt\\C', anchored.anchor)
  assert.deepEqual(keys(ranged.selected), ['d:/wt/a', 'd:/wt/b', 'd:/wt/c'])
})
