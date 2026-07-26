import assert from 'node:assert/strict'
import test from 'node:test'
import {
  moveMobileTab,
  orderMobileTabs,
  parseMobileTabOrder,
  pruneMobileTabOrder,
  serializeMobileTabOrder,
} from '../src/mobileTabOrder.ts'

const tabs = (...ids: string[]) => ids.map(id => ({ id }))

test('an empty or absent order leaves layout order untouched', () => {
  assert.deepEqual(orderMobileTabs(tabs('a', 'b', 'c'), undefined).map(tab => tab.id), ['a', 'b', 'c'])
  assert.deepEqual(orderMobileTabs(tabs('a', 'b', 'c'), []).map(tab => tab.id), ['a', 'b', 'c'])
})

test('a full saved order is applied verbatim', () => {
  assert.deepEqual(orderMobileTabs(tabs('a', 'b', 'c'), ['c', 'a', 'b']).map(tab => tab.id), ['c', 'a', 'b'])
})

test('a tab the save predates lands beside its layout neighbour, not at the end', () => {
  // 'b2' was launched from 'b', so the layout puts it right after 'b'. The rail
  // must show it there rather than appending it after the reordered tail.
  assert.deepEqual(
    orderMobileTabs(tabs('a', 'b', 'b2', 'c'), ['c', 'b', 'a']).map(tab => tab.id),
    ['c', 'b', 'b2', 'a'],
  )
})

test('a run of new tabs stays together and in layout order', () => {
  assert.deepEqual(
    orderMobileTabs(tabs('a', 'n1', 'n2', 'b'), ['b', 'a']).map(tab => tab.id),
    ['b', 'a', 'n1', 'n2'],
  )
})

test('new tabs with no known predecessor go to the front', () => {
  assert.deepEqual(orderMobileTabs(tabs('new', 'a', 'b'), ['b', 'a']).map(tab => tab.id), ['new', 'b', 'a'])
})

test('saved ids that no longer exist are ignored, and duplicates collapse', () => {
  assert.deepEqual(orderMobileTabs(tabs('a', 'b'), ['gone', 'b', 'b', 'a']).map(tab => tab.id), ['b', 'a'])
})

test('moving swaps one slot and refuses to run off either end', () => {
  assert.deepEqual(moveMobileTab(['a', 'b', 'c'], 'c', 'left'), ['a', 'c', 'b'])
  assert.deepEqual(moveMobileTab(['a', 'b', 'c'], 'a', 'right'), ['b', 'a', 'c'])
  assert.equal(moveMobileTab(['a', 'b', 'c'], 'a', 'left'), null)
  assert.equal(moveMobileTab(['a', 'b', 'c'], 'c', 'right'), null)
  assert.equal(moveMobileTab(['a', 'b'], 'missing', 'left'), null)
})

test('stored orders round-trip and reject malformed payloads', () => {
  const order = { 'project-a': ['b', 'a'] }
  assert.deepEqual(parseMobileTabOrder(serializeMobileTabOrder(order)), order)
  assert.deepEqual(parseMobileTabOrder(null), {})
  assert.deepEqual(parseMobileTabOrder('not json'), {})
  assert.deepEqual(parseMobileTabOrder('[1,2]'), {})
  assert.deepEqual(parseMobileTabOrder('{"p":"nope","q":[1,"a"],"r":[]}'), { q: ['a'] })
})

test('pruning drops projects that no longer exist', () => {
  const order = { keep: ['a'], gone: ['b'] }
  assert.deepEqual(pruneMobileTabOrder(order, ['keep', 'other']), { keep: ['a'] })
})
