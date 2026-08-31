import assert from 'node:assert/strict'
import test from 'node:test'
import { DRAWER_TABS, type DrawerTabId } from '../src/drawerTabs.ts'
import { DEFAULT_DRAWER_TAB_ORDER, normalizeDrawerTabOrder } from '../src/drawerTabOrder.ts'

test('the default order is the registry order', () => {
  assert.deepEqual(DEFAULT_DRAWER_TAB_ORDER, DRAWER_TABS.map(tab => tab.id))
  assert.deepEqual(normalizeDrawerTabOrder(undefined), DEFAULT_DRAWER_TAB_ORDER)
})

test('a stored arrangement round-trips', () => {
  const custom: DrawerTabId[] = ['files', 'notes', 'git', 'processes', 'schedule', 'actions', 'queue', 'transcript', 'activity', 'agent', 'notifications']
  assert.deepEqual(normalizeDrawerTabOrder(custom), custom)
})

test('every retired tab id folds into the tab that absorbed it, keeping its position', () => {
  // `prompts`, `commands`, and `clipboard` all became Actions; `insight` and `changemap`
  // both became Activity. An order is a list of tabs, so several retired ids collapsing
  // onto one survivor must produce one entry, at the first of their positions. Both of
  // these stored fragments happen to preserve canonical relative order, so the merge
  // reproduces the full default exactly.
  assert.deepEqual(
    normalizeDrawerTabOrder(['files', 'prompts', 'clipboard', 'commands', 'queue']),
    DEFAULT_DRAWER_TAB_ORDER,
  )
  assert.deepEqual(
    normalizeDrawerTabOrder(['changemap', 'insight', 'timeline', 'context', 'agent']),
    DEFAULT_DRAWER_TAB_ORDER,
  )
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
  const withoutActions = DEFAULT_DRAWER_TAB_ORDER.filter(id => id !== 'actions')
  assert.deepEqual(normalizeDrawerTabOrder(withoutActions), DEFAULT_DRAWER_TAB_ORDER)

  // The case this rule was written for, now that it has happened: an order saved before
  // the prompt queue moved into the drawer gains Queue beside Actions, in the injection
  // block, rather than after Alerts.
  const beforeQueue = DEFAULT_DRAWER_TAB_ORDER.filter(id => id !== 'queue')
  assert.deepEqual(normalizeDrawerTabOrder(beforeQueue), DEFAULT_DRAWER_TAB_ORDER)

  // The merge is relative to where the predecessor sits in the *user's* arrangement, not
  // where it sat in the default one: `queue` rejoins `actions` wherever the user moved it.
  const custom = ['notifications', 'files', 'actions']
  assert.deepEqual(
    normalizeDrawerTabOrder(custom),
    ['notes', 'notifications', 'files', 'actions', 'queue', 'transcript', 'activity', 'agent', 'git', 'processes', 'schedule'],
  )

  // A first tab the order predates goes to the front rather than after everything.
  assert.deepEqual(normalizeDrawerTabOrder(['files'])[0], 'notes')
})
