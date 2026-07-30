import assert from 'node:assert/strict'
import test from 'node:test'
import { DRAWER_TABS, nextDrawerTab, type DrawerTabId } from '../src/drawerTabs.ts'
import {
  DEFAULT_DRAWER_TAB_ORDER,
  isDefaultDrawerTabOrder,
  normalizeDrawerTabOrder,
  orderedDrawerTabs,
  sameDrawerTabOrder,
} from '../src/drawerTabOrder.ts'

test('the default order is the registry order', () => {
  assert.deepEqual(DEFAULT_DRAWER_TAB_ORDER, DRAWER_TABS.map(tab => tab.id))
  assert.ok(isDefaultDrawerTabOrder(DEFAULT_DRAWER_TAB_ORDER))
  assert.ok(isDefaultDrawerTabOrder(normalizeDrawerTabOrder(undefined)))
})

test('a stored arrangement round-trips and reports as custom', () => {
  const custom: DrawerTabId[] = ['files', 'notes', 'git', 'clipboard', 'commands', 'prompts', 'queue', 'notifications']
  assert.deepEqual(normalizeDrawerTabOrder(custom), custom)
  assert.ok(!isDefaultDrawerTabOrder(custom))
  assert.deepEqual(orderedDrawerTabs(custom).map(tab => tab.id), custom)
})

test('arranging never loses, hides, or duplicates a tab', () => {
  // Whatever comes back from storage or another client, every tab is rendered exactly once:
  // a drag rearranges the strip, it can never empty it.
  for (const raw of [
    null,
    'nonsense',
    [],
    ['files'],
    ['files', 'files', 'files'],
    ['nope', 'files', 42, null, 'notes'],
    [...DEFAULT_DRAWER_TAB_ORDER].reverse(),
  ]) {
    const normalized = normalizeDrawerTabOrder(raw)
    assert.equal(normalized.length, DRAWER_TABS.length, JSON.stringify(raw))
    assert.deepEqual([...normalized].sort(), [...DEFAULT_DRAWER_TAB_ORDER].sort(), JSON.stringify(raw))
    assert.equal(orderedDrawerTabs(normalized).filter(Boolean).length, DRAWER_TABS.length)
  }
})

test('a tab the stored order predates lands beside its default neighbour, not at the end', () => {
  // The order a user saved today has to survive a tab being added tomorrow. Appending would
  // put every new surface in the one position that reads as an afterthought.
  const withoutPrompts = DEFAULT_DRAWER_TAB_ORDER.filter(id => id !== 'prompts')
  assert.deepEqual(normalizeDrawerTabOrder(withoutPrompts), DEFAULT_DRAWER_TAB_ORDER)

  // The case this rule was written for, now that it has happened: an order saved before
  // the prompt queue moved into the drawer gains Queue beside Prompts, in the injection
  // block, rather than after Alerts.
  const beforeQueue = DEFAULT_DRAWER_TAB_ORDER.filter(id => id !== 'queue')
  assert.deepEqual(normalizeDrawerTabOrder(beforeQueue), DEFAULT_DRAWER_TAB_ORDER)

  // The merge is relative to where the predecessor sits in the *user's* arrangement, not
  // where it sat in the default one: `notes` rejoins `files` and `commands`/`prompts` rejoin
  // `clipboard`, wherever the user moved those to.
  const custom = ['notifications', 'files', 'clipboard']
  assert.deepEqual(
    normalizeDrawerTabOrder(custom),
    ['notifications', 'files', 'notes', 'git', 'clipboard', 'commands', 'prompts', 'queue'],
  )

  // A first tab the order predates goes to the front rather than after everything.
  assert.deepEqual(normalizeDrawerTabOrder(['notes'])[0], 'clipboard')
})

test('keyboard cycling walks the arranged order, not the registry order', () => {
  const custom: DrawerTabId[] = ['notes', 'files', 'git', 'clipboard', 'commands', 'prompts', 'queue', 'notifications']
  assert.equal(nextDrawerTab('notes', 1, custom), 'files')
  assert.equal(nextDrawerTab('notes', -1, custom), 'notifications')
  assert.equal(nextDrawerTab('notifications', 1, custom), 'notes')
  // No arrangement supplied falls back to the registry order.
  assert.equal(nextDrawerTab('clipboard', 1), 'commands')
  assert.equal(nextDrawerTab('clipboard', 1, []), 'commands')
})

test('order comparison is exact, so an unchanged drag writes nothing', () => {
  assert.ok(sameDrawerTabOrder(DEFAULT_DRAWER_TAB_ORDER, [...DEFAULT_DRAWER_TAB_ORDER]))
  assert.ok(!sameDrawerTabOrder(DEFAULT_DRAWER_TAB_ORDER, [...DEFAULT_DRAWER_TAB_ORDER].reverse()))
})
