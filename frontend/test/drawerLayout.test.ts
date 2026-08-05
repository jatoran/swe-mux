import assert from 'node:assert/strict'
import test from 'node:test'
import { DRAWER_TABS, type DrawerTabId } from '../src/drawerTabs.ts'
import {
  DRAWER_MAX_DEPTH,
  activateDrawerTab,
  adjacentDrawerStack,
  defaultDrawerLayout,
  drawerStackForTab,
  drawerStacks,
  drawerTabs,
  migrateDrawerProjectPresentations,
  moveDrawerTabDirection,
  moveDrawerTabToSplit,
  moveDrawerTabToStack,
  normalizeDrawerLayout,
  normalizeDrawerProjectPresentation,
  parseDrawerLayout,
  parseDrawerProjectPresentations,
  pruneDrawerProjectPresentations,
  reconcileDrawerProjectPresentations,
  reorderDrawerStack,
  resetDrawerLayout,
  serializeDrawerLayout,
  serializeDrawerProjectPresentations,
  setDrawerSplitRatio,
  type DrawerLayout,
  type DrawerNode,
} from '../src/drawerLayout.ts'

const ids = DRAWER_TABS.map(tab => tab.id)
const assertSingletons = (layout: DrawerLayout) => {
  assert.deepEqual([...drawerTabs(layout)].sort(), [...ids].sort())
  assert.equal(new Set(drawerTabs(layout)).size, ids.length)
  assert.ok(drawerStacks(layout).every(stack => stack.tabs.length > 0))
}

test('default and flat-order migration create one canonical stack', () => {
  const layout = defaultDrawerLayout()
  assert.equal(drawerStacks(layout).length, 1)
  assert.deepEqual(drawerTabs(layout), ids)
  const custom = [...ids].reverse()
  assert.deepEqual(drawerTabs(parseDrawerLayout(null, custom)), custom)
})

test('layout parse and serialization repair malformed singleton state', () => {
  const raw = {
    version: 1,
    root: {
      type: 'split', id: 'same', direction: 'bad', ratio: 7,
      first: { type: 'stack', id: 'same', tabs: ['files', 'files', 'removed'] },
      second: { type: 'stack', id: 'same', tabs: ['notes'] },
    },
  }
  const layout = normalizeDrawerLayout(raw)
  assertSingletons(layout)
  assert.equal((layout.root as { ratio: number }).ratio, 0.9)
  const nodeIds = drawerStacks(layout).map(stack => stack.id)
  assert.equal(new Set(nodeIds).size, nodeIds.length)
  assert.deepEqual(parseDrawerLayout(serializeDrawerLayout(layout)), layout)
  assertSingletons(parseDrawerLayout('{bad'))
  assertSingletons(normalizeDrawerLayout({ nope: true }))
})

test('missing shipped tabs join their canonical predecessor without moving existing tabs', () => {
  const layout = normalizeDrawerLayout({ type: 'stack', id: 'one', tabs: ['notifications', 'files', 'clipboard'] })
  assert.deepEqual(drawerTabs(layout), [
    'notifications', 'files', 'notes', 'context', 'git', 'processes', 'mailbox',
    'clipboard', 'commands', 'prompts', 'queue', 'transcript', 'agent',
  ])
})

test('empty branches collapse and excess depth recovers to valid stacks', () => {
  let node: DrawerNode = { type: 'stack', id: 'deep-stack', tabs: ids }
  for (let depth = 0; depth < DRAWER_MAX_DEPTH + 5; depth += 1) {
    node = {
      type: 'split', id: `split-${depth}`, direction: 'horizontal', ratio: 0.5,
      first: { type: 'stack', id: `empty-${depth}`, tabs: [] }, second: node,
    }
  }
  const layout = normalizeDrawerLayout({ version: 1, root: node })
  assertSingletons(layout)
  assert.ok(drawerStacks(layout).length >= 1)
})

test('reorder requires the exact stack set and preserves semantic no-ops', () => {
  const layout = defaultDrawerLayout()
  const stack = drawerStacks(layout)[0]
  assert.equal(reorderDrawerStack(layout, stack.id, stack.tabs), layout)
  assert.equal(reorderDrawerStack(layout, stack.id, ['files']), layout)
  const reordered = reorderDrawerStack(layout, stack.id, [...stack.tabs].reverse())
  assert.deepEqual(drawerTabs(reordered), [...ids].reverse())
})

test('same-stack and cross-stack moves preserve exact singleton ownership', () => {
  const base = defaultDrawerLayout()
  const stack = drawerStacks(base)[0]
  const first = moveDrawerTabToStack(base, 'files', stack.id, 0)
  assert.equal(drawerTabs(first)[0], 'files')
  const middle = moveDrawerTabToStack(first, 'files', stack.id, 5)
  assert.equal(drawerTabs(middle)[5], 'files')
  const last = moveDrawerTabToStack(middle, 'files', stack.id, Number.POSITIVE_INFINITY)
  assert.equal(drawerTabs(last).at(-1), 'files')
  const split = moveDrawerTabToSplit(last, 'notes', stack.id, 'right')
  assert.equal(drawerStacks(split).length, 2)
  const notesStack = drawerStackForTab(split, 'notes')!
  const clipboardStack = drawerStackForTab(split, 'clipboard')!
  const moved = moveDrawerTabToStack(split, 'files', notesStack.id, 0)
  assert.deepEqual(drawerStackForTab(moved, 'files')?.id, notesStack.id)
  assert.deepEqual(drawerStackForTab(moved, 'clipboard')?.id, clipboardStack.id)
  assertSingletons(moved)
})

test('all split edges, source collapse, ratio updates, and sole-tab no-op work', () => {
  for (const edge of ['left', 'right', 'top', 'bottom'] as const) {
    const base = defaultDrawerLayout()
    const source = drawerStacks(base)[0]
    const split = moveDrawerTabToSplit(base, 'notes', source.id, edge)
    assert.equal(drawerStacks(split).length, 2, edge)
    assertSingletons(split)
    assert.equal(split.root.type, 'split')
    if (split.root.type === 'split') {
      assert.equal(split.root.direction, edge === 'left' || edge === 'right' ? 'horizontal' : 'vertical')
      const firstOwnsNotes = drawerTabs({ version: 1, root: split.root.first }).includes('notes')
      assert.equal(firstOwnsNotes, edge === 'left' || edge === 'top')
    }
    assert.equal(moveDrawerTabToSplit(split, 'notes', drawerStackForTab(split, 'notes')!.id, edge), split)
    const splitId = split.root.type === 'split' ? split.root.id : ''
    assert.equal((setDrawerSplitRatio(split, splitId, -4).root as { ratio: number }).ratio, 0.1)
  }
  let layout = defaultDrawerLayout()
  const original = drawerStacks(layout)[0]
  layout = moveDrawerTabToSplit(layout, 'notes', original.id, 'right')
  const source = drawerStackForTab(layout, 'notes')!
  const target = drawerStackForTab(layout, 'files')!
  layout = moveDrawerTabToStack(layout, 'notes', target.id)
  assert.equal(drawerStacks(layout).length, 1)
  assert.equal(drawerStacks(layout).some(stack => stack.id === source.id), false)
})

test('repeated edge splits form a nine-stack grid and flatten depth first', () => {
  let layout = defaultDrawerLayout()
  const tabs = ids.slice(1, 9)
  for (const [index, tab] of tabs.entries()) {
    const target = drawerStacks(layout)[index % drawerStacks(layout).length]
    layout = moveDrawerTabToSplit(layout, tab, target.id, index % 2 ? 'bottom' : 'right')
  }
  assert.equal(drawerStacks(layout).length, 9)
  assertSingletons(layout)
  assert.deepEqual(drawerTabs(parseDrawerLayout(serializeDrawerLayout(layout))), drawerTabs(layout))
})

test('Project presentation normalizes selection and activation without changing layout', () => {
  let layout = defaultDrawerLayout()
  const root = drawerStacks(layout)[0]
  layout = moveDrawerTabToSplit(layout, 'notes', root.id, 'right')
  const before = serializeDrawerLayout(layout)
  let presentation = normalizeDrawerProjectPresentation({
    focused_tab: 'notes', selected_tabs: { missing: 'git' }, desktop_expanded: true,
  }, layout)
  assert.equal(presentation.focused_tab, 'notes')
  assert.equal(presentation.selected_tabs[drawerStackForTab(layout, 'notes')!.id], 'notes')
  presentation = activateDrawerTab(presentation, layout, 'files')
  assert.equal(presentation.focused_tab, 'files')
  assert.equal(serializeDrawerLayout(layout), before)
})

test('different Projects restore selections and expansion without changing the global tree', () => {
  let layout = defaultDrawerLayout()
  layout = moveDrawerTabToSplit(layout, 'notes', drawerStacks(layout)[0].id, 'right')
  const original = serializeDrawerLayout(layout)
  const first = activateDrawerTab(normalizeDrawerProjectPresentation({ desktop_expanded: true }, layout), layout, 'notes')
  const second = activateDrawerTab(normalizeDrawerProjectPresentation({ desktop_expanded: false }, layout), layout, 'git')
  assert.equal(first.focused_tab, 'notes')
  assert.equal(first.desktop_expanded, true)
  assert.equal(second.focused_tab, 'git')
  assert.equal(second.desktop_expanded, false)
  assert.equal(serializeDrawerLayout(layout), original)
})

test('v1 Project state migrates to v2 without duplicating the global layout', () => {
  const layout = defaultDrawerLayout()
  const map = migrateDrawerProjectPresentations(null, JSON.stringify({
    p1: { tab: 'git', desktopExpanded: true }, p2: { tab: 'removed' },
  }), layout)
  assert.equal(map.p1.focused_tab, 'git')
  assert.equal(map.p1.desktop_expanded, true)
  assert.equal(map.p2.focused_tab, 'clipboard')
  const serialized = serializeDrawerProjectPresentations(map, layout)
  assert.deepEqual(parseDrawerProjectPresentations(serialized, layout), map)
  assert.doesNotMatch(serialized, /"root"|"direction"|"ratio"/)
})

test('layout changes reconcile every Project deterministically and pruning is presentation-only', () => {
  let layout = defaultDrawerLayout()
  const map = migrateDrawerProjectPresentations(null, JSON.stringify({
    p1: { tab: 'files', desktopExpanded: true }, p2: { tab: 'notes' },
  }), layout)
  layout = moveDrawerTabToSplit(layout, 'notes', drawerStacks(layout)[0].id, 'right')
  const reconciled = reconcileDrawerProjectPresentations(map, layout)
  assert.equal(reconciled.p1.desktop_expanded, true)
  assert.equal(reconciled.p2.focused_tab, 'notes')
  const layoutBefore = serializeDrawerLayout(layout)
  assert.deepEqual(Object.keys(pruneDrawerProjectPresentations(reconciled, ['p1'])), ['p1'])
  assert.equal(serializeDrawerLayout(layout), layoutBefore)
  assertSingletons(resetDrawerLayout())
})

test('directional movement uses adjacent panes then creates a split when needed', () => {
  let layout = defaultDrawerLayout()
  const root = drawerStacks(layout)[0]
  layout = moveDrawerTabToSplit(layout, 'notes', root.id, 'right')
  const left = drawerStackForTab(layout, 'files')!
  const right = drawerStackForTab(layout, 'notes')!
  assert.equal(adjacentDrawerStack(layout, left.id, 'right')?.id, right.id)
  layout = moveDrawerTabDirection(layout, 'files', 'right')
  assert.equal(drawerStackForTab(layout, 'files')?.id, right.id)
  assertSingletons(layout)
})

test('a deterministic mixed sequence preserves every registered tab after every operation', () => {
  let layout = defaultDrawerLayout()
  const assertStep = (next: DrawerLayout) => { layout = normalizeDrawerLayout(next); assertSingletons(layout) }
  const first = drawerStacks(layout)[0]
  assertStep(moveDrawerTabToSplit(layout, 'notes', first.id, 'right'))
  assertStep(moveDrawerTabToSplit(layout, 'git', drawerStackForTab(layout, 'clipboard')!.id, 'bottom'))
  assertStep(moveDrawerTabToStack(layout, 'files', drawerStackForTab(layout, 'notes')!.id, 0))
  assertStep(moveDrawerTabDirection(layout, 'commands', 'bottom'))
  const stack = drawerStackForTab(layout, 'clipboard')!
  assertStep(reorderDrawerStack(layout, stack.id, [...stack.tabs].reverse()))
  if (layout.root.type === 'split') assertStep(setDrawerSplitRatio(layout, layout.root.id, 0.73))
})
