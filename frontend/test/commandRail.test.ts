import assert from 'node:assert/strict'
import test from 'node:test'
import { BUILTIN_RAIL, clearProjectRailBlob, railHasProjectOverride, railItemsFromBlob, railItemVisible, railPayload, resolveRail, writeRailBlob, type RailItem } from '../src/commandRail.ts'

test('default rail preserves the legacy order and omits disabled extras', () => {
  const ids = resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'claude' }).map(item => item.id)
  assert.deepEqual(ids, [
    'relaunch', 'copyReply', 'copyResume', 'branch', 'paste', 'kbdToggle',
    'esc', 'enter', 'tab', 'ctrlC', 'up', 'down', 'left', 'right',
  ])
})

test('branch item is limited to agent backends', () => {
  const shellIds = resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'shell' }).map(item => item.id)
  assert.equal(shellIds.includes('branch'), false)
  const codexIds = resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'codex' }).map(item => item.id)
  assert.equal(codexIds.includes('branch'), true)
})

test('platform filter hides items outside their platform', () => {
  const item: RailItem = { id: 'x', type: 'text', label: 'x', platforms: ['mobile'] }
  assert.equal(railItemVisible(item, { platform: 'mobile', backend: 'claude' }), true)
  assert.equal(railItemVisible(item, { platform: 'desktop', backend: 'claude' }), false)
})

test('backend filter hides items outside their backends', () => {
  const item: RailItem = { id: 'x', type: 'slash', label: 'x', backends: ['claude'] }
  assert.equal(railItemVisible(item, { platform: 'desktop', backend: 'claude' }), true)
  assert.equal(railItemVisible(item, { platform: 'desktop', backend: 'codex' }), false)
})

test('enabled:false hides an item everywhere', () => {
  const item: RailItem = { id: 'x', type: 'key', label: 'x', enabled: false }
  assert.equal(railItemVisible(item, { platform: 'mobile', backend: 'shell' }), false)
})

test('skill payload is backend-aware; slash is literal both ways', () => {
  const skill: RailItem = { id: 's', type: 'skill', label: 's', text: 'commit' }
  assert.equal(railPayload(skill, 'claude'), '/commit')
  assert.equal(railPayload(skill, 'codex'), '$commit')
  const slash: RailItem = { id: 'n', type: 'slash', label: 'n', text: 'new' }
  assert.equal(railPayload(slash, 'claude'), '/new')
  assert.equal(railPayload(slash, 'codex'), '/new')
})

test('skill/slash payload tolerates a leading sigil in the stored name', () => {
  assert.equal(railPayload({ id: 's', type: 'skill', label: 's', text: '/commit' }, 'codex'), '$commit')
  assert.equal(railPayload({ id: 'n', type: 'slash', label: 'n', text: '/new' }, 'claude'), '/new')
})

test('text payload is passed through verbatim', () => {
  assert.equal(railPayload({ id: 't', type: 'text', label: 't', text: 'hello world' }, 'claude'), 'hello world')
})

test('a project override fully replaces the global list; other projects keep global', () => {
  const custom: RailItem[] = [{ id: 'paste', type: 'action', action: 'paste', label: 'Paste' }]
  const blob = writeRailBlob({ items: undefined }, custom, 'proj-a')
  assert.deepEqual(railItemsFromBlob(blob, 'proj-a').map(i => i.id), ['paste', ...BUILTIN_RAIL.filter(b => b.id !== 'paste').map(b => b.id)])
  // Another project (no override) resolves to the global/default list.
  assert.deepEqual(railItemsFromBlob(blob, 'proj-b').map(i => i.id), BUILTIN_RAIL.map(b => b.id))
  assert.equal(railHasProjectOverride(blob, 'proj-a'), true)
  assert.equal(railHasProjectOverride(blob, 'proj-b'), false)
})

test('clearing a project override reverts it to the global rail', () => {
  const blob = writeRailBlob(undefined, [{ id: 'esc', type: 'key', label: 'Esc', bytes: '\x1b' }], 'proj-a')
  const cleared = clearProjectRailBlob(blob, 'proj-a')
  assert.equal(railHasProjectOverride(cleared, 'proj-a'), false)
  assert.deepEqual(railItemsFromBlob(cleared, 'proj-a').map(i => i.id), BUILTIN_RAIL.map(b => b.id))
})

test('writing the global list leaves project overrides intact', () => {
  const withProject = writeRailBlob(undefined, [{ id: 'esc', type: 'key', label: 'Esc', bytes: '\x1b' }], 'proj-a')
  const withGlobal = writeRailBlob(withProject, [{ id: 'paste', type: 'action', action: 'paste', label: 'Paste' }])
  assert.equal(railHasProjectOverride(withGlobal, 'proj-a'), true)
  assert.deepEqual(railItemsFromBlob(withGlobal, 'proj-a').map(i => i.id)[0], 'esc')
})
