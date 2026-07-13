import assert from 'node:assert/strict'
import test from 'node:test'
import {
  attachLeaf, emptyLayout, leaves, noteResourceId, parseLayout, parseNoteResourceId,
  removeLeaf, replaceTerminal, resourceLeaf, setSplitRatio, splitTerminal,
  swapTerminals, terminalIds,
} from '../src/layout.ts'

test('arbitrary split trees round-trip and preserve terminal membership', () => {
  let layout = splitTerminal(emptyLayout(), null, 'one', 'horizontal')
  layout = splitTerminal(layout, 'one', 'two', 'horizontal')
  layout = splitTerminal(layout, 'two', 'three', 'vertical')
  assert.deepEqual(terminalIds(parseLayout(JSON.parse(JSON.stringify(layout)))), ['one', 'two', 'three'])
  assert.equal(layout.root?.type, 'split')
})

test('ratio, swap, detach, and replacement do not lose displaced live identities', () => {
  let layout = splitTerminal({ version: 2, root: { type: 'leaf', kind: 'terminal', id: 'one' } }, 'one', 'two', 'horizontal')
  layout = setSplitRatio(layout, '', .72)
  assert.equal(layout.root?.type === 'split' ? layout.root.ratio : 0, .72)
  assert.deepEqual(terminalIds(swapTerminals(layout, 'one', 'two')), ['two', 'one'])
  assert.deepEqual(terminalIds(removeLeaf(layout, 'terminal', 'one')), ['two'])
  assert.deepEqual(terminalIds(replaceTerminal(layout, 'one', 'three')), ['three', 'two'])
})

test('legacy membership migrates to the recursive v2 contract', () => {
  const migrated = parseLayout({ version: 1, panes: ['one', 'two', 'three'] })
  assert.equal(migrated.version, 2)
  assert.deepEqual(terminalIds(migrated), ['one', 'two', 'three'])
})

test('notes attach beside a terminal as stable, removable resources', () => {
  const base = { version: 2, root: { type: 'leaf', kind: 'terminal', id: 'term-a' } } as const
  const resourceId = noteResourceId('sessions', 'session/a')
  const docked = attachLeaf(base, 'term-a', resourceLeaf('note', resourceId), 'horizontal', .62)
  assert.deepEqual(terminalIds(docked), ['term-a'])
  assert.deepEqual(leaves(docked, 'note').map(leaf => leaf.id), [resourceId])
  assert.deepEqual(parseNoteResourceId(resourceId), { kind: 'sessions', id: 'session/a' })
  assert.equal(docked.root?.type === 'split' ? docked.root.ratio : 0, .62)
  assert.deepEqual(leaves(removeLeaf(docked, 'note', resourceId), 'note'), [])
})
