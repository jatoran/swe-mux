import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DRAWER_DEFAULT_WIDTH,
  DRAWER_MAX_WIDTH,
  DRAWER_MIN_WIDTH,
  DRAWER_TABS,
  clampDrawerWidth,
  drawerTab,
  isNavigatorTab,
  nextDrawerTab,
  parseDrawerTab,
  storedDrawerWidth,
} from '../src/drawerTabs.ts'

test('injection surfaces lead, then navigators, then attention surfaces', () => {
  // Order is the argument for the drawer existing: clipboard, session commands and
  // prompts are all "text into the focused terminal" and belong together. Files and
  // Notes are the second group — project-scoped indexes that open a document into a
  // pane instead of typing into one. Notifications is neither, and stays last.
  // Git closes the Project-scoped block: it reports on the repository behind the Project
  // rather than opening anything into a pane, so it sits with them without being a navigator.
  assert.deepEqual(DRAWER_TABS.map(tab => tab.id), ['clipboard', 'commands', 'prompts', 'files', 'notes', 'git', 'notifications'])
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.scope === 'session').map(tab => tab.id), ['clipboard', 'commands', 'prompts'])
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.scope === 'project').map(tab => tab.id), ['files', 'notes', 'git'])
  assert.deepEqual(DRAWER_TABS.filter(tab => isNavigatorTab(tab.id)).map(tab => tab.id), ['files', 'notes'])
  // The insert group and the navigator group must stay contiguous, so the rail reads as
  // two blocks rather than an arbitrary list.
  const scopes = DRAWER_TABS.map(tab => tab.scope)
  assert.deepEqual(scopes, [...new Set(scopes)].flatMap(scope => scopes.filter(item => item === scope)))
  // Both surfaces render an icon, so the label is only ever an accessible name. It still has
  // to be short and distinct: it is what a screen reader announces for the tab.
  const labels = DRAWER_TABS.map(tab => tab.label)
  assert.equal(new Set(labels).size, labels.length, 'tab labels must be distinct')
  for (const tab of DRAWER_TABS) {
    assert.ok(tab.label.length <= 10, `${tab.id} label is too long to also serve as a name`)
    assert.ok(tab.title.startsWith(tab.label), `${tab.id} title should lead with its label`)
  }
  // The icons live in `railIcons.tsx`, which this module must not import: it stays JSX-free so
  // it runs under plain type-stripping. A contract test checks the map covers every id.
})

test('a stored tab is restored, anything else falls back to clipboard', () => {
  assert.equal(parseDrawerTab('commands'), 'commands')
  assert.equal(parseDrawerTab('notifications'), 'notifications')
  assert.equal(parseDrawerTab(null), 'clipboard')
  assert.equal(parseDrawerTab('processes'), 'clipboard')
  assert.equal(drawerTab('prompts').label, 'Prompts')
  // An unknown id must still yield a tab rather than crashing the host.
  assert.equal(drawerTab('nope' as never).id, 'clipboard')
})

test('dock width is bounded and survives junk in localStorage', () => {
  assert.equal(clampDrawerWidth(120), DRAWER_MIN_WIDTH)
  assert.equal(clampDrawerWidth(9000), DRAWER_MAX_WIDTH)
  assert.equal(clampDrawerWidth(420), 420)
  assert.equal(clampDrawerWidth(Number.NaN), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('440'), 440)
  assert.equal(storedDrawerWidth(null), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('not-a-number'), DRAWER_DEFAULT_WIDTH)
  assert.equal(storedDrawerWidth('0'), DRAWER_DEFAULT_WIDTH)
})

test('tab cycling wraps in both directions', () => {
  assert.equal(nextDrawerTab('clipboard', 1), 'commands')
  assert.equal(nextDrawerTab('prompts', 1), 'files')
  assert.equal(nextDrawerTab('notes', 1), 'git')
  assert.equal(nextDrawerTab('notifications', 1), 'clipboard')
  assert.equal(nextDrawerTab('clipboard', -1), 'notifications')
  assert.equal(nextDrawerTab('prompts', -2), 'clipboard')
})
