import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DRAWER_DEFAULT_WIDTH,
  DRAWER_MAX_WIDTH,
  DRAWER_MIN_WIDTH,
  DRAWER_TABS,
  clampDrawerWidth,
  drawerTab,
  nextDrawerTab,
  parseDrawerTab,
  storedDrawerWidth,
} from '../src/drawerTabs.ts'

test('injection surfaces lead the drawer, attention surfaces follow', () => {
  // Order is the argument for the drawer existing: clipboard, session commands and
  // prompts are all "text into the focused terminal" and belong together.
  assert.deepEqual(DRAWER_TABS.map(tab => tab.id), ['clipboard', 'commands', 'prompts', 'notifications'])
  assert.deepEqual(DRAWER_TABS.filter(tab => tab.sessionScoped).map(tab => tab.id), ['clipboard', 'commands', 'prompts'])
  // One-character glyphs only: the desktop rail is 40px and must never wrap.
  for (const tab of DRAWER_TABS) assert.equal([...tab.glyph].length, 1, tab.id)
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
  assert.equal(nextDrawerTab('notifications', 1), 'clipboard')
  assert.equal(nextDrawerTab('clipboard', -1), 'notifications')
  assert.equal(nextDrawerTab('prompts', -2), 'clipboard')
})
