import assert from 'node:assert/strict'
import test from 'node:test'
import { BUILTIN_RAIL, adoptPlacement, clearProjectRailBlob, mergeRail, railHasProjectOverride, railItemsFromBlob, railItemVisible, railPayload, resolveRail, writeRailBlob, type RailItem } from '../src/commandRail.ts'
import { AGENT_NEWLINE } from '../src/terminalKeys.ts'

test('default rail groups editing helpers after Down and ends with Attach', () => {
  const ids = resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'claude' }).map(item => item.id)
  assert.deepEqual(ids, [
    'relaunch', 'copyReply', 'copyResume', 'branch', 'paste', 'clipboardHistory', 'kbdToggle',
    'esc', 'enter', 'tab', 'ctrlC', 'up', 'down',
    'markdownDivider', 'markdownCodeFence', 'clearInput', 'restoreInput',
    'left', 'right', 'attach',
  ])
})

test('attach is an agent-only configurable action', () => {
  const attach = BUILTIN_RAIL.find(item => item.id === 'attach')
  assert.ok(attach)
  assert.equal(attach.type, 'action')
  assert.equal(resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'claude' }).includes(attach), true)
  assert.equal(resolveRail(BUILTIN_RAIL, { platform: 'mobile', backend: 'codex' }).includes(attach), true)
  assert.equal(resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'shell' }).includes(attach), false)
  assert.equal(resolveRail([{ ...attach, enabled: false }], { platform: 'desktop', backend: 'claude' }).length, 0)
})

test('the editing-helper catalog migration reorders old saved rails once', () => {
  const merged = mergeRail([{ id: 'paste', type: 'action', action: 'paste', label: 'Paste' }])
  const ids = merged.map(item => item.id)
  const down = ids.indexOf('down')
  assert.deepEqual(ids.slice(down + 1, down + 5), ['markdownDivider', 'markdownCodeFence', 'clearInput', 'restoreInput'])
  assert.equal(merged.find(item => item.id === 'clearInput')?.placement, 'strip')
  assert.equal(ids.at(-1), 'attach')
})

test('saved ordering remains authoritative after the editing helpers are present', () => {
  const saved = BUILTIN_RAIL.map(item => ({ ...item }))
  saved.unshift(...saved.splice(saved.findIndex(item => item.id === 'attach'), 1))
  assert.equal(mergeRail(saved)[0]?.id, 'attach')
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

test('placement splits the strip from the drawer without hiding anything', () => {
  const ctx = { platform: 'desktop', backend: 'claude' } as const
  const strip = resolveRail(BUILTIN_RAIL, ctx).map(item => item.id)
  const drawer = resolveRail(BUILTIN_RAIL, ctx, 'drawer').map(item => item.id)
  // The hot keys stay on the strip; the long tail that used to ship switched off
  // is now visible in the drawer instead of hidden behind a settings trip.
  assert.equal(strip.includes('esc'), true)
  assert.equal(strip.includes('home'), false)
  assert.deepEqual(drawer, ['home', 'end', 'ctrlHome', 'ctrlEnd', 'newline', 'rewind', 'endSession'])
  // Every built-in lands in exactly one host.
  assert.deepEqual([...strip, ...drawer].sort(), BUILTIN_RAIL.filter(item => railItemVisible(item, ctx)).map(item => item.id).sort())
})

test('the drawer newline uses the sequence accepted by Claude and Codex', () => {
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'newline')?.bytes, AGENT_NEWLINE)
})

test('multiline editing helpers use non-submitting agent newline keys', () => {
  const divider = BUILTIN_RAIL.find(item => item.id === 'markdownDivider')
  const codeFence = BUILTIN_RAIL.find(item => item.id === 'markdownCodeFence')
  assert.equal(divider?.type, 'key')
  assert.equal(divider?.bytes, `${AGENT_NEWLINE}${AGENT_NEWLINE}---${AGENT_NEWLINE}${AGENT_NEWLINE}`)
  assert.equal(codeFence?.type, 'key')
  assert.equal(codeFence?.bytes, `${AGENT_NEWLINE}${AGENT_NEWLINE}\`\`\`${AGENT_NEWLINE}`)
  assert.equal(divider?.agentOnly, true)
  assert.equal(codeFence?.agentOnly, true)
  assert.equal(resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'shell' }).includes(divider!), false)
  assert.equal(resolveRail(BUILTIN_RAIL, { platform: 'desktop', backend: 'shell' }).includes(codeFence!), false)
})

test('single-byte editing helpers retain their control keys', () => {
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'clearInput')?.bytes, '\x15')
  assert.equal(BUILTIN_RAIL.find(item => item.id === 'restoreInput')?.bytes, '\x19')
})

test('end session ships in the drawer for every backend, never on the strip', () => {
  // It is the one destructive built-in, so it must not default next to the arrow keys;
  // moving it onto the strip stays a deliberate settings choice.
  for (const backend of ['claude', 'codex', 'shell'] as const) {
    const ctx = { platform: 'mobile', backend } as const
    assert.equal(resolveRail(BUILTIN_RAIL, ctx).some(item => item.id === 'endSession'), false)
    assert.equal(resolveRail(BUILTIN_RAIL, ctx, 'drawer').some(item => item.id === 'endSession'), true)
  }
})

test('a pre-placement save that switched an item off means "not on the strip"', () => {
  // Before the drawer existed, off was the only way out of a full strip, so those
  // saves migrate to the drawer rather than staying invisible.
  assert.deepEqual(adoptPlacement({ enabled: false }), { enabled: undefined, placement: 'drawer' })
  // An explicit placement always wins, including an explicit off.
  assert.deepEqual(adoptPlacement({ enabled: false, placement: 'strip' }), { enabled: false, placement: 'strip' })
  assert.deepEqual(adoptPlacement({ enabled: undefined }), { enabled: undefined, placement: 'strip' })
  // Custom items (skills, slash commands) default to the drawer: they are
  // unbounded in number and would crowd the arrows off the strip.
  assert.deepEqual(adoptPlacement({ enabled: undefined }, 'drawer'), { enabled: undefined, placement: 'drawer' })

  const merged = mergeRail([{ id: 'home', type: 'key', label: 'Home', enabled: false }])
  assert.equal(merged.find(item => item.id === 'home')?.placement, 'drawer')
  const custom = mergeRail([{ id: 'custom:skill:commit:0', type: 'skill', label: 'commit', text: 'commit' }])
  assert.equal(custom.find(item => item.id === 'custom:skill:commit:0')?.placement, 'drawer')
})

test("placement 'both' renders an item in the strip and the drawer", () => {
  const items: RailItem[] = [{ id: 'x', type: 'skill', label: 'x', text: 'commit', placement: 'both' }]
  const ctx = { platform: 'desktop', backend: 'claude' } as const
  assert.deepEqual(resolveRail(items, ctx).map(item => item.id), ['x'])
  assert.deepEqual(resolveRail(items, ctx, 'drawer').map(item => item.id), ['x'])
  // 'both' survives a save round-trip instead of collapsing to the fallback.
  assert.deepEqual(adoptPlacement({ placement: 'both' }, 'drawer'), { enabled: undefined, placement: 'both' })
  assert.equal(mergeRail(items).find(item => item.id === 'x')?.placement, 'both')
  // Filters and the off switch still win over a two-host placement.
  assert.deepEqual(resolveRail([{ ...items[0], enabled: false }], ctx), [])
  assert.deepEqual(resolveRail([{ ...items[0], enabled: false }], ctx, 'drawer'), [])
  assert.deepEqual(resolveRail([{ ...items[0], backends: ['codex'] }], ctx, 'drawer'), [])
})

test('an item switched off is absent from both hosts', () => {
  const items: RailItem[] = [{ id: 'x', type: 'key', label: 'x', bytes: 'x', enabled: false, placement: 'drawer' }]
  const ctx = { platform: 'desktop', backend: 'shell' } as const
  assert.deepEqual(resolveRail(items, ctx), [])
  assert.deepEqual(resolveRail(items, ctx, 'drawer'), [])
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
  const projectIds = railItemsFromBlob(blob, 'proj-a').map(i => i.id)
  assert.equal(projectIds[0], 'paste')
  assert.deepEqual(projectIds.slice(projectIds.indexOf('down') + 1, projectIds.indexOf('down') + 5), ['markdownDivider', 'markdownCodeFence', 'clearInput', 'restoreInput'])
  assert.equal(projectIds.at(-1), 'attach')
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
