import assert from 'node:assert/strict'
import test from 'node:test'
import { DRAWER_TABS, type DrawerTabId } from '../src/drawerTabs.ts'
import { DEFAULT_DRAWER_TAB_ORDER, normalizeDrawerTabOrder } from '../src/drawerTabOrder.ts'

test('the default order is the registry order', () => {
  assert.deepEqual(DEFAULT_DRAWER_TAB_ORDER, DRAWER_TABS.map(tab => tab.id))
  assert.deepEqual(normalizeDrawerTabOrder(undefined), DEFAULT_DRAWER_TAB_ORDER)
})

test('a stored arrangement round-trips', () => {
  const custom: DrawerTabId[] = ['files', 'notes', 'context', 'git', 'processes', 'mailbox', 'clipboard', 'commands', 'prompts', 'queue', 'transcript', 'agent', 'notifications']
  assert.deepEqual(normalizeDrawerTabOrder(custom), custom)
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
    ['notifications', 'files', 'notes', 'context', 'git', 'processes', 'mailbox', 'clipboard', 'commands', 'prompts', 'queue', 'transcript', 'agent'],
  )

  // A first tab the order predates goes to the front rather than after everything.
  assert.deepEqual(normalizeDrawerTabOrder(['notes'])[0], 'clipboard')
})
